"""Tests for the jira-props command line interface."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from jira_project_properties import cli
from jira_project_properties.client import (
    JiraAPIError,
    JiraProjectPropertiesClient,
)


class FakeClient:
    def __init__(self, base_url, token, **kwargs):
        self.base_url = base_url
        self.token = token
        self.email = kwargs.get("email")
        self.kwargs = kwargs
        self.calls = []
        self.find_results = {"ABC": {"enabled": True}, "XYZ": "on"}

    def list_keys(self, project):
        self.calls.append(("list_keys", project))
        return ["alpha", "beta"]

    def get_value(self, project, key):
        self.calls.append(("get_value", project, key))
        return {"enabled": True, "retries": 3}

    def set_value(self, project, key, value):
        self.calls.append(("set_value", project, key, value))
        return {"echo": value}

    def delete_value(self, project, key):
        self.calls.append(("delete_value", project, key))

    def find_projects_with_property(self, property_key):
        self.calls.append(("find_projects_with_property", property_key))
        return dict(self.find_results)


class _TtyInput(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def cli_env(monkeypatch):
    """Patch out the HTTP client and set env vars; returns a test runner."""
    state = {"class": FakeClient, "client": None}

    def factory(base_url, token, **kwargs):
        state["client"] = state["class"](base_url, token, **kwargs)
        return state["client"]

    monkeypatch.setattr(cli, "JiraProjectPropertiesClient", factory)
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "secret")

    class Runner:
        @staticmethod
        def run(argv, stdin=None):
            if stdin is not None:
                monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
            code = cli.main(argv)
            return code, state["client"]

        @staticmethod
        def use(client_class):
            state["class"] = client_class

    return Runner()


# --------------------------------------------------------------------------- #
# subcommands                                                                  #
# --------------------------------------------------------------------------- #


def test_get_prints_pretty_json(cli_env, capsys):
    code, client = cli_env.run(["get", "ABC", "feature.flag"])
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == [("get_value", "ABC", "feature.flag")]
    assert '"enabled": true' in out
    assert '"retries": 3' in out


def test_list_prints_one_key_per_line(cli_env, capsys):
    code, _ = cli_env.run(["list", "ABC"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "alpha\nbeta\n"
    assert captured.err == ""


def test_set_with_inline_value(cli_env, capsys):
    code, client = cli_env.run(["set", "ABC", "k", "--value", '{"a": 1}'])
    assert code == 0
    assert client.calls == [("set_value", "ABC", "k", {"a": 1})]
    assert "Set property 'k' on project 'ABC'" in capsys.readouterr().out


def test_set_with_file(cli_env, tmp_path):
    path = tmp_path / "value.json"
    path.write_text('{"source": "file"}')
    code, client = cli_env.run(["set", "ABC", "k", "--file", str(path)])
    assert code == 0
    assert client.calls == [("set_value", "ABC", "k", {"source": "file"})]


def test_set_from_stdin(cli_env):
    code, client = cli_env.run(["set", "ABC", "k"], stdin="[1, 2]")
    assert code == 0
    assert client.calls == [("set_value", "ABC", "k", [1, 2])]


def test_set_rejects_non_json_value(cli_env, capsys):
    code, client = cli_env.run(["set", "ABC", "k", "--value", "hello"])
    captured = capsys.readouterr()
    assert code == 2
    assert "not valid JSON" in captured.err
    assert client.calls == []


def test_set_without_value_or_stdin_is_an_error(cli_env, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TtyInput())
    code, _ = cli_env.run(["set", "ABC", "k"])
    assert code == 2
    assert "--value" in capsys.readouterr().err


def test_delete(cli_env, capsys):
    code, client = cli_env.run(["delete", "ABC", "k"])
    assert code == 0
    assert client.calls == [("delete_value", "ABC", "k")]
    assert "Deleted property 'k' from project 'ABC'" in capsys.readouterr().out


def test_find_prints_matches_as_json(cli_env, capsys):
    code, client = cli_env.run(["find", "owner"])
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == [("find_projects_with_property", "owner")]
    assert '"ABC"' in out
    assert '"XYZ"' in out
    assert '"enabled": true' in out


def test_find_with_no_matches_prints_empty_object(cli_env, capsys):
    class NoMatchClient(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.find_results = {}

    cli_env.use(NoMatchClient)
    code, _ = cli_env.run(["find", "missing.key"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "{}"
    assert "no projects" in captured.err


# --------------------------------------------------------------------------- #
# configuration                                                                #
# --------------------------------------------------------------------------- #


def test_flags_override_environment(cli_env):
    code, client = cli_env.run(
        ["--url", "https://other.example.com", "--token", "tok2", "list", "ABC"]
    )
    assert code == 0
    assert client.base_url == "https://other.example.com"
    assert client.token == "tok2"


def test_connection_options_work_after_the_subcommand(cli_env):
    code, client = cli_env.run(
        ["list", "ABC", "--url", "https://after.example.com", "--token", "tok3"]
    )
    assert code == 0
    assert client.base_url == "https://after.example.com"
    assert client.token == "tok3"


def test_missing_url_is_an_error(cli_env, monkeypatch, capsys):
    monkeypatch.delenv("JIRA_URL")
    code, _ = cli_env.run(["list", "ABC"])
    assert code == 2
    assert "JIRA_URL" in capsys.readouterr().err


def test_missing_token_is_an_error(cli_env, monkeypatch, capsys):
    monkeypatch.delenv("JIRA_TOKEN")
    code, _ = cli_env.run(["list", "ABC"])
    assert code == 2
    assert "JIRA_TOKEN" in capsys.readouterr().err


def test_jira_pat_alias_is_accepted(cli_env, monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN")
    monkeypatch.setenv("JIRA_PAT", "pat-token")
    code, client = cli_env.run(["list", "ABC"])
    assert code == 0
    assert client.token == "pat-token"


def test_cloud_email_flag_is_passed_to_client(cli_env):
    code, client = cli_env.run(["--email", "user@example.com", "list", "ABC"])
    assert code == 0
    assert client.email == "user@example.com"


def test_email_from_environment(cli_env, monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "env-user@example.com")
    code, client = cli_env.run(["list", "ABC"])
    assert code == 0
    assert client.email == "env-user@example.com"


def test_api_token_env_var_is_accepted(cli_env, monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN")
    monkeypatch.setenv("JIRA_API_TOKEN", "api-tok")
    code, client = cli_env.run(["list", "ABC"])
    assert code == 0
    assert client.token == "api-tok"


def test_cloud_site_without_email_prints_warning(cli_env, capsys):
    code, client = cli_env.run(
        ["--url", "https://example.atlassian.net", "--token", "tok", "list", "ABC"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "warning" in captured.err
    assert "--email" in captured.err
    assert client.email is None


# --------------------------------------------------------------------------- #
# exit codes                                                                   #
# --------------------------------------------------------------------------- #


def test_api_errors_exit_with_code_1(cli_env, capsys):
    class FailingClient(FakeClient):
        def get_value(self, project, key):
            raise JiraAPIError(404, "HTTP 404: property does not exist")

    cli_env.use(FailingClient)
    code, _ = cli_env.run(["get", "ABC", "missing"])
    captured = capsys.readouterr()
    assert code == 1
    assert "property does not exist" in captured.err


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "jira-props" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# debug output                                                                 #
# --------------------------------------------------------------------------- #


def test_debug_flag_parses_before_and_after_subcommand():
    assert cli.build_parser().parse_args(["--debug", "list", "ABC"]).debug is True
    assert cli.build_parser().parse_args(["list", "ABC", "--debug"]).debug is True
    assert cli.build_parser().parse_args(["list", "ABC"]).debug is False


def test_debug_option_prints_api_endpoints(monkeypatch, capsys):
    class StubResponse:
        ok = True
        status_code = 200
        text = '{"keys": []}'
        reason = "OK"

        def json(self):
            return {"keys": []}

    class StubSession:
        def request(self, method, url, **kwargs):
            return StubResponse()

    def factory(base_url, token, **kwargs):
        return JiraProjectPropertiesClient(
            base_url, token, session=StubSession(), **kwargs
        )

    monkeypatch.setattr(cli, "JiraProjectPropertiesClient", factory)
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "secret")

    logger = logging.getLogger("jira_project_properties")
    saved_level, saved_handlers = logger.level, logger.handlers[:]
    try:
        code = cli.main(["--debug", "list", "ABC"])
        captured = capsys.readouterr()
    finally:
        logger.setLevel(saved_level)
        logger.handlers[:] = saved_handlers

    assert code == 0
    assert "DEBUG" in captured.err
    assert (
        "GET https://jira.example.com/rest/api/2/project/ABC/properties"
        in captured.err
    )
