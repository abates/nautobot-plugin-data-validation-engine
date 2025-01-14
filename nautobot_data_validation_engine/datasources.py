"""Datasource definitions."""
from nautobot.extras.choices import LogLevelChoices
from nautobot.extras.registry import DatasourceContent
from nautobot_data_validation_engine.custom_validators import get_classes_from_git_repo


def refresh_git_data_compliance_rules(repository_record, job_result, delete=False):  # pylint: disable=W0613
    """Callback for repo refresh."""
    job_result.log("Successfully pulled git repo", level_choice=LogLevelChoices.LOG_SUCCESS)
    for compliance_class in get_classes_from_git_repo(repository_record):
        job_result.log(f"Found class {str(compliance_class.__name__)}", level_choice=LogLevelChoices.LOG_SUCCESS)


datasource_contents = [
    (
        "extras.gitrepository",
        DatasourceContent(
            name="data compliance rules",
            content_identifier="nautobot_data_validation_engine.data_compliance_rules",
            icon="mdi-file-document-outline",
            callback=refresh_git_data_compliance_rules,
        ),
    )
]
