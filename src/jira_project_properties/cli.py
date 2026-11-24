"""Command line interface: the ``jira-props`` program."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .client import JiraAPIError, JiraProjectPropertiesClient

PROG = "jira-props"

#: Environment variables consulted for connection settings.
ENV_URL = "JIRA_URL"
ENV_EMAIL = "JIRA_EMAIL"
#: Token environment variables, in order of precedence.
ENV_TOKEN_VARS = ("JIRA_API_TOKEN", "JIRA_TOKEN", "JIRA_PAT")


class UsageError(Exception):
    """Invalid command line input (exit code 2)."""


# --------------------------------------------------------------------------- #
# subcommands                                                                  #
# --------------------------------------------------------------------------- #


def cmd_list(args: argparse.Namespace, client: JiraProjectPropertiesClient) -> int:
    keys = client.list_keys(args.project)
    if not keys:
        print("(no properties)", file=sys.stderr)
    for key in keys:
        print(key)
    return 0


def cmd_get(args: argparse.Namespace, client: JiraProjectPropertiesClient) -> int:
    value = client.get_value(args.project, args.key)
    print(json.dumps(value, indent=2))
    return 0


def cmd_set(args: argparse.Namespace, client: JiraProjectPropertiesClient) -> int:
    raw = _read_value_text(args)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError(
            "the value is not valid JSON: {}. To store a plain string, wrap it "
            "in double quotes: --value '\"hello\"'".format(exc)
        ) from exc
    result = client.set_value(args.project, args.key, value)
    print("Set property {!r} on project {!r}".format(args.key, args.project))
    if result is not None:
        print(json.dumps(result, indent=2))
    return 0


def cmd_delete(args: argparse.Namespace, client: JiraProjectPropertiesClient) -> int:
    client.delete_value(args.project, args.key)
    print("Deleted property {!r} from project {!r}".format(args.key, args.project))
    return 0


# --------------------------------------------------------------------------- #
# argument parsing                                                             #
# --------------------------------------------------------------------------- #


def _add_connection_options(
    parser: argparse.ArgumentParser, *, suppress: bool
) -> None:
    """Register --url/--token/--api-version/--timeout on *parser*.

    Called once on the main parser (with real defaults) and once on every
    subparser (with ``SUPPRESS`` defaults) so the options work both before and
    after the subcommand without the subparser clobbering already-parsed
    values.
    """
    no_default = argparse.SUPPRESS if suppress else None
    parser.add_argument(
        "--url",
        default=no_default,
        metavar="URL",
        help="Jira base URL, e.g. https://your-domain.atlassian.net"
        " (default: $JIRA_URL)",
    )
    parser.add_argument(
        "--email",
        default=no_default,
        metavar="EMAIL",
        help="Atlassian account email; enables Jira Cloud API-token Basic"
        " auth (default: $JIRA_EMAIL)",
    )
    parser.add_argument(
        "--token",
        default=no_default,
        metavar="TOKEN",
        help="API token (Jira Cloud, with --email) or personal access token"
        " (Data Center/Server) (default: $JIRA_API_TOKEN, $JIRA_TOKEN, or"
        " $JIRA_PAT)",
    )
    parser.add_argument(
        "--api-version",
        type=int,
        default=argparse.SUPPRESS if suppress else 2,
        metavar="N",
        help="Jira REST API version (default: 2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=argparse.SUPPRESS if suppress else 30.0,
        metavar="SECONDS",
        help="HTTP timeout in seconds (default: 30)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="View, set, and delete Jira project entity properties.",
        epilog=(
            "authentication: Jira Cloud uses an account email plus API token\n"
            "(HTTP Basic auth); Jira Data Center/Server uses a personal access\n"
            "token (PAT) sent as a bearer token. Providing --email selects the\n"
            "Cloud method.\n"
            "\n"
            "examples:\n"
            "  {prog} --url https://your-domain.atlassian.net \\\n"
            "      --email you@example.com --token API_TOKEN list ABC\n"
            "\n"
            "with JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN set:\n"
            "\n"
            "  {prog} list ABC\n"
            "  {prog} get ABC deployment.notes\n"
            "  {prog} set ABC deployment.notes --value '{{\"environment\": \"prod\"}}'\n"
            "  {prog} set ABC deployment.config --file config.json\n"
            "  {prog} delete ABC deployment.notes\n"
        ).format(prog=PROG),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s {}".format(__version__)
    )
    _add_connection_options(parser, suppress=False)

    subparsers = parser.add_subparsers(
        dest="command", metavar="COMMAND", required=True
    )

    list_parser = subparsers.add_parser(
        "list", help="list all property keys of a project"
    )
    list_parser.add_argument("project", metavar="PROJECT", help="project id or key")
    list_parser.set_defaults(func=cmd_list)

    get_parser = subparsers.add_parser(
        "get", help="print the value of a project property"
    )
    get_parser.add_argument("project", metavar="PROJECT", help="project id or key")
    get_parser.add_argument("key", metavar="KEY", help="property key")
    get_parser.set_defaults(func=cmd_get)

    set_parser = subparsers.add_parser(
        "set", help="create or update a project property (value must be JSON)"
    )
    set_parser.add_argument("project", metavar="PROJECT", help="project id or key")
    set_parser.add_argument("key", metavar="KEY", help="property key")
    source = set_parser.add_mutually_exclusive_group()
    source.add_argument(
        "--value",
        metavar="JSON",
        help="inline JSON value, e.g. '{\"enabled\": true}'",
    )
    source.add_argument(
        "--file",
        metavar="PATH",
        help="read the JSON value from a file ('-' for stdin)",
    )
    set_parser.set_defaults(func=cmd_set)

    delete_parser = subparsers.add_parser("delete", help="remove a project property")
    delete_parser.add_argument("project", metavar="PROJECT", help="project id or key")
    delete_parser.add_argument("key", metavar="KEY", help="property key")
    delete_parser.set_defaults(func=cmd_delete)

    for subparser in subparsers.choices.values():
        _add_connection_options(subparser, suppress=True)

    return parser


def _read_value_text(args: argparse.Namespace) -> str:
    """Fetch the raw JSON text for ``set`` from --value, --file, or stdin."""
    if args.value is not None:
        return args.value
    if args.file is not None:
        if args.file == "-":
            return sys.stdin.read()
        try:
            return Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError("cannot read {}: {}".format(args.file, exc)) from exc
    if sys.stdin.isatty():
        raise UsageError(
            "no property value given; use --value JSON, --file PATH, or pipe"
            " JSON on standard input"
        )
    return sys.stdin.read()


def _token_from_env() -> str | None:
    for name in ENV_TOKEN_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return None


# --------------------------------------------------------------------------- #
# entry point                                                                  #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    url = args.url or os.environ.get(ENV_URL)
    email = args.email or os.environ.get(ENV_EMAIL)
    token = args.token or _token_from_env()
    if not url:
        print(
            "error: no Jira URL given; pass --url or set ${}".format(ENV_URL),
            file=sys.stderr,
        )
        return 2
    if not token:
        print(
            "error: no token given; pass --token or set ${}".format(
                " or $".join(ENV_TOKEN_VARS)
            ),
            file=sys.stderr,
        )
        return 2

    if not email and ".atlassian.net" in url:
        print(
            "warning: {} looks like a Jira Cloud site; Cloud expects an"
            " account email plus API token, so also pass --email (or set"
            " $JIRA_EMAIL)".format(url),
            file=sys.stderr,
        )

    try:
        client = JiraProjectPropertiesClient(
            url, token, email=email, api_version=args.api_version, timeout=args.timeout
        )
        return args.func(args, client)
    except UsageError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    except JiraAPIError as exc:
        print("error: {}".format(exc.message), file=sys.stderr)
        return 1
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
