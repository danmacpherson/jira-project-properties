"""Allow ``python -m jira_project_properties`` as an alternative entry point."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
