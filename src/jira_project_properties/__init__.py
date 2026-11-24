"""jira_project_properties — view, set, and delete Jira project entity properties."""

from .client import JiraAPIError, JiraProjectPropertiesClient

__version__ = "0.1.0"

__all__ = ["JiraAPIError", "JiraProjectPropertiesClient", "__version__"]
