#!/usr/bin/env python3
"""A tiny in-memory stand-in for the Jira project-properties REST API.

Useful for trying out ``jira-props`` without a real Jira instance:

    uv run python scripts/mock_jira_server.py --port 8923

    uv run jira-props --url http://127.0.0.1:8923 --email demo@example.com \\
        --token anything set ABC demo.key --value '{"hello": ["world"]}'

    uv run jira-props --url http://127.0.0.1:8923 --email demo@example.com \\
        --token anything list ABC

Any Basic (Cloud-style) or bearer (PAT-style) credentials are accepted.
Data is kept in memory only.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_MISSING = object()
PROPERTIES = {}  # {project: {key: value}}


def handle(method, path, body):
    parts = [part for part in path.split("?")[0].split("/") if part]
    # Expect: rest/api/2/project/{projectIdOrKey}/properties[/{propertyKey}]
    if (
        len(parts) < 6
        or parts[:4] != ["rest", "api", "2", "project"]
        or parts[5] != "properties"
    ):
        return 404, {"errorMessages": ["unknown route: " + path]}

    project = parts[4]
    key = parts[6] if len(parts) > 6 else None
    props = PROPERTIES.setdefault(project, {})

    if method == "GET" and key is None:
        return 200, {"keys": [{"key": k, "self": "..."} for k in props]}
    if method == "GET":
        if key not in props:
            return 404, {"errorMessages": ["property '{}' does not exist".format(key)]}
        return 200, {"key": key, "value": props[key]}
    if method == "PUT":
        props[key] = json.loads(body)
        return 200, props[key]
    if method == "DELETE":
        if props.pop(key, _MISSING) is _MISSING:
            return 404, {"errorMessages": ["property '{}' does not exist".format(key)]}
        return 204, None
    return 405, {"errorMessages": ["method not allowed"]}


class Handler(BaseHTTPRequestHandler):
    def _dispatch(self, method):
        auth = self.headers.get("Authorization") or ""
        if not (auth.startswith("Basic ") or auth.startswith("Bearer ")):
            self._reply(401, {"errorMessages": ["missing Authorization header"]})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        status, payload = handle(method, self.path, body)
        self._reply(status, payload)

    def _reply(self, status, payload):
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

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Run a mock Jira project-properties server."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8923)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        "mock Jira listening on http://{}:{} (any bearer token accepted)".format(
            args.host, args.port
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
