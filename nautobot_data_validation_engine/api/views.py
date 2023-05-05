"""API views."""
from collections import defaultdict
from django.apps import apps

from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.viewsets import GenericViewSet

from nautobot.extras.api.views import NautobotModelViewSet

from nautobot_data_validation_engine.api import serializers
from nautobot_data_validation_engine import models, filters
from nautobot_data_validation_engine.custom_validators import get_data_compliance_rules_map, ValidationError
from nautobot_data_validation_engine.utils import extract_attribute_errors


class DataValidationEngineRootView(APIRootView):
    """Data Validation Engine API root view."""

    def get_view_name(self):
        """Get the name of the view."""
        return "Data Validation Engine"


class RegularExpressionValidationRuleViewSet(NautobotModelViewSet):
    """View to manage regular expression validation rules."""

    queryset = models.RegularExpressionValidationRule.objects.all()
    serializer_class = serializers.RegularExpressionValidationRuleSerializer
    filterset_class = filters.RegularExpressionValidationRuleFilterSet


class MinMaxValidationRuleViewSet(NautobotModelViewSet):
    """View to manage min max expression validation rules."""

    queryset = models.MinMaxValidationRule.objects.all()
    serializer_class = serializers.MinMaxValidationRuleSerializer
    filterset_class = filters.MinMaxValidationRuleFilterSet


class RequiredValidationRuleViewSet(NautobotModelViewSet):
    """View to manage min max expression validation rules."""

    queryset = models.RequiredValidationRule.objects.all()
    serializer_class = serializers.RequiredValidationRuleSerializer
    filterset_class = filters.RequiredValidationRuleFilterSet


class UniqueValidationRuleViewSet(NautobotModelViewSet):
    """View to manage min max expression validation rules."""

    queryset = models.UniqueValidationRule.objects.all()
    serializer_class = serializers.UniqueValidationRuleSerializer
    filterset_class = filters.UniqueValidationRuleFilterSet


class DataComplianceAPIView(NautobotModelViewSet):
    """API Views for DataCompliance."""

    queryset = models.DataCompliance.objects.all()
    serializer_class = serializers.DataComplianceSerializer


class DataComplianceObjectAPIView(GenericViewSet):
    serializer_class = serializers.DataComplianceObjectSerializer

    @property
    def queryset(self):
        return self.model.objects.all()

    def _audit(self, auditor):
        try:
            auditor()
        except ValidationError as ex:
            self.compliance_errors.update(extract_attribute_errors(ex))

    def initialize_request(self, request, *args, **kwargs):
        request = super().initialize_request(request, *args, **kwargs)
        model = self.kwargs.get("model", None)
        if model:
            self.model = apps.get_model(model)
            self.app_label = self.model._meta.app_label
            self.model_name = self.model._meta.model_name
            self.rules = get_data_compliance_rules_map().get(f"{self.app_label}.{self.model_name}", [])

        return request

    def _validate(self, instance):
        self.compliance_errors = defaultdict(list)
        self._audit(instance.full_clean)
        for rule_class in self.rules:
            rule = rule_class(instance)
            self._audit(rule.audit)

        return {
            "model": self.model._meta.label_lower,
            "id": instance.id,
            "valid": len(self.compliance_errors) == 0,
            "errors": self.compliance_errors,
        }

    def get_object(self):
        return self._validate(super().get_object())

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(self._validate(instance))
        return Response(serializer.data)
    
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            page = [self._validate(item) for item in page]
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer([self._validate(item) for item in queryset], many=True)
        return Response(serializer.data)
