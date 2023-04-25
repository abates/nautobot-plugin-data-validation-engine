"""
This is the meat of this plugin.

Here we dynamically generate a PluginCustomValidator class
for each model currently registered in the extras_features
query registry 'custom_validators'.

A common clean method for all these classes looks for any
validation rules that have been defined for the given model.
"""
import re
import logging
import inspect

from typing import Optional
from django.template.defaultfilters import pluralize
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from nautobot.extras.plugins import PluginCustomValidator, CustomValidator
from nautobot.extras.registry import registry
from nautobot.extras.models import GitRepository
from nautobot.utilities.utils import render_jinja2

from nautobot_data_validation_engine.models import (
    MinMaxValidationRule,
    RegularExpressionValidationRule,
    RequiredValidationRule,
    UniqueValidationRule,
    validate_regex,
)

from nautobot_data_validation_engine.models import AuditResult
from nautobot_data_validation_engine.utils import import_python_file_from_git_repo

LOGGER = logging.getLogger(__name__)


class BaseValidator(PluginCustomValidator):
    """Base PluginCustomValidator class that implements the core logic for enforcing validation rules defined in this plugin."""

    model = None

    def clean(self):
        """The clean method executes the actual rule enforcement logic for each model."""
        obj = self.context["object"]

        # Regex rules
        for rule in RegularExpressionValidationRule.objects.get_for_model(self.model):
            field_value = getattr(obj, rule.field)

            if field_value is None:
                # Coerce to a string for regex validation
                field_value = ""

            if rule.context_processing:
                # Render the regular_expression as a jinja2 string and ensure it is valid
                try:
                    regular_expression = render_jinja2(rule.regular_expression, self.context)
                    validate_regex(regular_expression)
                except Exception:
                    LOGGER.exception(
                        f"There was an error rendering the regular expression in the data validation rule '{rule}' and a ValidationError was raised!"
                    )
                    self.validation_error(
                        {
                            rule.field: f"There was an error rendering the regular expression in the data validation rule '{rule}'. "
                            "Either fix the validation rule or disable it in order to save this data."
                        }
                    )

            else:
                regular_expression = rule.regular_expression

            if not re.match(regular_expression, field_value):
                self.validation_error(
                    {rule.field: rule.error_message or f"Value does not conform to regex: {regular_expression}"}
                )

        # Min/Max rules
        for rule in MinMaxValidationRule.objects.get_for_model(self.model):
            field_value = getattr(obj, rule.field)

            if field_value is None:
                self.validation_error(
                    {
                        rule.field: rule.error_message
                        or f"Value does not conform to mix/max validation: min {rule.min}, max {rule.max}"
                    }
                )

            elif not isinstance(field_value, (int, float)):
                self.validation_error(
                    {
                        rule.field: f"Unable to validate against min/max rule {rule} because the field value is not numeric."
                    }
                )

            elif rule.min is not None and field_value is not None and field_value < rule.min:
                self.validation_error(
                    {rule.field: rule.error_message or f"Value is less than minimum value: {rule.min}"}
                )

            elif rule.max is not None and field_value is not None and field_value > rule.max:
                self.validation_error(
                    {rule.field: rule.error_message or f"Value is more than maximum value: {rule.max}"}
                )

        # Required rules
        for rule in RequiredValidationRule.objects.get_for_model(self.model):
            field_value = getattr(obj, rule.field)
            if field_value is None or field_value == "":
                self.validation_error({rule.field: rule.error_message or "This field cannot be blank."})

        # Unique rules
        for rule in UniqueValidationRule.objects.get_for_model(self.model):
            field_value = getattr(obj, rule.field)
            if (
                field_value is not None
                and obj.__class__._default_manager.filter(**{rule.field: field_value}).count() >= rule.max_instances
            ):
                self.validation_error(
                    {
                        rule.field: rule.error_message
                        or f"There can only be {rule.max_instances} instance{pluralize(rule.max_instances)} with this value."
                    }
                )
        
        # Audit Rulesets
        for audit_class in get_audit_rule_sets_map()[self.model]:
            audit_class(obj).clean()
        
        for repo in GitRepository.objects.filter(
            provided_contents__contains="nautobot_data_validation_engine.audit_rulesets"
        ):
            module = import_python_file_from_git_repo(repo)
            if hasattr(module, "custom_validators"):
                for audit_class in module.custom_validators:
                    if (
                        f"{self.context['object']._meta.app_label}.{self.context['object']._meta.model_name}"
                        != audit_class.model
                    ):
                        continue
                    ins = audit_class(self.context["object"])
                    ins.clean()


def is_audit_rule_set(obj):
    """Check to see if object is an AuditRuleset class instance."""
    return inspect.isclass(obj) and issubclass(obj, AuditRuleset)


def get_audit_rule_sets_map():
    """Generate a dictionary of audit rulesets associated to their models."""
    audit_rulesets = {}
    for validators in registry["plugin_custom_validators"].values():
        for validator in validators:
            if is_audit_rule_set(validator):
                audit_rulesets.setdefault(validator.model, [])
                audit_rulesets[validator.model].append(validator)

    return audit_rulesets


def get_audit_rule_sets():
    """Generate a list of Audit Ruleset classes that exist from the registry."""
    validators = []
    for rule_sets in get_audit_rule_sets_map().values():
        validators.extend(rule_sets)
    return validators


class AuditError(ValidationError):
    """An audit error is raised only when an object fails an audit."""


class AuditRuleset(CustomValidator):
    """Class to handle a set of validation functions."""

    class_name: Optional[str] = None
    model: str
    result_date: timezone
    enforce = False

    def __init__(self, obj):
        """Initialize an AuditRuleset object."""
        super().__init__(obj)
        self.class_name = self.class_name or self.__class__.__name__
        self.result_date = timezone.now()

    def audit(self):
        """Not implemented.  Should raise an AuditError if an attribute is found to be invalid."""
        raise NotImplementedError

    def mark_existing_attributes_as_valid(self, exclude_attributes=None):
        """Mark all existing fields (any that were previously created) as valid=True."""
        instance = self.context["object"]
        if not exclude_attributes:
            exclude_attributes = []
        attributes = (
            AuditResult.objects.filter(
                audit_class_name=self.class_name,
                content_type=ContentType.objects.get_for_model(instance),
                object_id=instance.id,
            )
            .exclude(validated_attribute__in=["all"] + exclude_attributes)
            .values_list("validated_attribute", flat=True)
        )
        for attribute in attributes:
            self.audit_result(message=f"{attribute} is valid.", attribute=attribute)

    def clean(self):
        """Override the clean method to run the audit function."""
        try:
            self.audit()
            self.mark_existing_attributes_as_valid()
            self.audit_result(message=f"{self.context['object']} is valid")
        except AuditError as ex:
            exclude_attributes = []
            try:
                for attribute, messages in ex.message_dict.items():
                    exclude_attributes.append(attribute)
                    for message in messages:
                        self.audit_result(message=message, attribute=attribute, valid=False)
            except AttributeError:
                for message in ex.messages:
                    self.audit_result(message=message, valid=False)
            finally:
                self.mark_existing_attributes_as_valid(exclude_attributes=exclude_attributes)
                self.audit_result(message=f"{self.context['object']} is not valid", valid=False)
            if self.enforce:
                raise ex

    @staticmethod
    def audit_error(message):
        """Raise an Audit Error with the given message."""
        raise AuditError(message)

    def audit_result(self, message, attribute=None, valid=True):
        """Generate an Audit Result object based on the given parameters."""
        instance = self.context["object"]
        attribute_value = None
        if attribute:
            attribute_value = getattr(instance, attribute)
        else:
            attribute = "all"
        result, _ = AuditResult.objects.update_or_create(
            audit_class_name=self.class_name,
            content_type=ContentType.objects.get_for_model(instance),
            object_id=instance.id,
            validated_attribute=attribute,
            defaults={
                "last_validation_date": self.result_date,
                "validated_attribute_value": str(attribute_value) if attribute_value else None,
                "message": message,
                "valid": valid,
            },
        )
        result.validated_save()


class CustomValidatorIterator:
    """Iterator that generates PluginCustomValidator classes for each model registered in the extras feature query registry 'custom_validators'."""

    def __iter__(self):
        """Return a generator of PluginCustomValidator classes for each registered model."""
        for app_label, models in registry["model_features"]["custom_validators"].items():
            for model in models:
                yield type(
                    f"{app_label.capitalize()}{model.capitalize()}CustomValidator",
                    (BaseValidator,),
                    {"model": f"{app_label}.{model}"},
                )


custom_validators = CustomValidatorIterator()
