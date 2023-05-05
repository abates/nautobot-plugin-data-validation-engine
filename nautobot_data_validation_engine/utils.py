"""Utility functions for nautobot_data_validation_engine."""

from collections import defaultdict
import importlib
from nautobot.extras.models import GitRepository
from nautobot.extras.datasources import ensure_git_repository


def import_python_file_from_git_repo(repo: GitRepository):
    """Load python file from git repo to use in job."""
    ensure_git_repository(repo)
    spec = importlib.util.spec_from_file_location("custom_validators", f"{repo.filesystem_path}/custom_validators.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_attribute_errors(validation_error):
    errors = defaultdict(list)
    exclude_attributes = []
    try:
        for attribute, messages in validation_error.message_dict.items():
            exclude_attributes.append(attribute)
            for message in messages:
                errors[attribute].append(message)
    except AttributeError:
        for message in validation_error.messages:
            errors["all"] = message
    return errors
