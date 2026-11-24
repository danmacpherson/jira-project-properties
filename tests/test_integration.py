"""End-to-end tests against a local HTTP server that mimics the Jira API."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jira_project_properties.client import JiraAPIError, JiraProjectPropertiesClient

_MISSING = object()


class FakeJira:
    """In-memory implementation of the project-properties endpoints."""

    def __init__(self):
        self.properties = {}  # {project: {key: value}}
        self.auth_headers = []

    def handle(self, method, path, body):
        parts = [p for p in path.split("/") if p]
        # Expect: rest/api/2/project/{projectIdOrKey}/properties[/{propertyKey}]
        if (
            len(parts) < 6
            or parts[:4] != ["rest", "api", "2", "project"]
            or parts[5] != "properties"
        ):
            return 404, {"errorMessages": ["unknown route: " + path]}
        project = parts[4]
        key = parts[6] if len(parts) > 6 else None
        props = self.properties.setdefault(project, {})

        if method == "GET" and key is None:
            return 200, {"keys": [{"key": k, "self": "..."} for k in props]}
        if method == "GET":
            if key not in props:
                return 404, {
                    "errorMessages": ["property '{}' does not exist".format(key)]
                }
            return 200, {"key": key, "value": props[key]}
        if method == "PUT":
            props[key] = json.loads(body)
            return 200, props[key]
        if method == "DELETE":
            if props.pop(key, _MISSING) is _MISSING:
                return 404, {
                    "errorMessages": ["property '{}' does not exist".format(key)]
                }
            return 204, None
        return 405, {"errorMessages": ["method not allowed"]}


def _make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            fake.auth_headers.append(self.headers.get("Authorization"))
            status, payload = fake.handle(method, self.path, body)
            data = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            self._dispatch("GET")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def log_message(self, fmt, *args):  # keep test output quiet
            pass

    return Handler


@pytest.fixture
def fake_jira():
    fake = FakeJira()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fake.url = "http://127.0.0.1:{}".format(server.server_address[1])
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def make_client(fake):
    return JiraProjectPropertiesClient(fake.url, "secret")


def make_cloud_client(fake):
    return JiraProjectPropertiesClient(fake.url, "secret", email="user@example.com")


def test_cloud_basic_auth_roundtrip(fake_jira):
    client = make_cloud_client(fake_jira)
    client.set_value("ABC", "k", {"a": 1})
    assert client.get_value("ABC", "k") == {"a": 1}
    expected = "Basic " + base64.b64encode(b"user@example.com:secret").decode("ascii")
    assert fake_jira.auth_headers[-1] == expected


def test_set_get_list_delete_roundtrip(fake_jira):
    client = make_client(fake_jira)
    assert client.list_keys("ABC") == []

    client.set_value("ABC", "deployment.notes", {"environment": "prod", "replicas": 3})
    assert client.list_keys("ABC") == ["deployment.notes"]
    assert client.get_value("ABC", "deployment.notes") == {
        "environment": "prod",
        "replicas": 3,
    }

    client.set_value("ABC", "owner", "platform-team")
    assert client.list_keys("ABC") == ["deployment.notes", "owner"]
    assert client.get_value("ABC", "owner") == "platform-team"

    client.delete_value("ABC", "deployment.notes")
    assert client.list_keys("ABC") == ["owner"]

    with pytest.raises(JiraAPIError) as excinfo:
        client.get_value("ABC", "deployment.notes")
    assert excinfo.value.status_code == 404


def test_requests_use_bearer_authentication(fake_jira):
    client = make_client(fake_jira)
    client.list_keys("ABC")
    assert fake_jira.auth_headers[-1] == "Bearer secret"


def test_projects_are_isolated(fake_jira):
    client = make_client(fake_jira)
    client.set_value("ABC", "shared", 1)
    client.set_value("XYZ", "shared", 2)
    assert client.get_value("ABC", "shared") == 1
    assert client.get_value("XYZ", "shared") == 2
