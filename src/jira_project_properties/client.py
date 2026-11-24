"""REST client for Jira project entity properties.

Wraps the project-properties endpoints plus the paginated project search
used to find projects by property:

=========== ==========================================================
list keys   GET    /rest/api/{v}/project/{projectIdOrKey}/properties
get value   GET    /rest/api/{v}/project/{projectIdOrKey}/properties/{propertyKey}
set value   PUT    /rest/api/{v}/project/{projectIdOrKey}/properties/{propertyKey}
delete      DELETE /rest/api/{v}/project/{projectIdOrKey}/properties/{propertyKey}
find        GET    /rest/api/{v}/project/search?properties={propertyKey}
=========== ==========================================================

API reference:
https://developer.atlassian.com/cloud/jira/platform/rest/v2/api-group-project-properties/

Two authentication methods are supported:

* **Jira Cloud** — an Atlassian account email plus an API token, sent as
  HTTP Basic auth (``email:api_token``). Create API tokens at
  https://id.atlassian.com/manage-profile/security/api-tokens/.
* **Jira Data Center / Server** — a personal access token (PAT), sent as an
  ``Authorization: Bearer <token>`` header.

Passing an *email* selects Basic auth; omitting it sends the token as a
bearer token.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any
from urllib.parse import quote, urlencode

import requests

logger = logging.getLogger(__name__)

#: Limits imposed by the Jira entity property API.
MAX_KEY_BYTES = 255
MAX_VALUE_BYTES = 32768

_STATUS_HINTS = {
    403: "the authenticated user lacks permission; writing or deleting project"
    " properties requires the Administer Projects permission",
    404: "check the project id/key and, if used, the property key",
}


class JiraAPIError(Exception):
    """A REST API call failed.

    ``status_code`` is the HTTP status returned by Jira, or ``0`` when the
    request never completed (DNS failure, connection refused, timeout, ...).
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class JiraProjectPropertiesClient:
    """View, set, and delete entity properties on Jira projects.

    Authenticates with an Atlassian account email plus API token (HTTP
    Basic, Jira Cloud) when *email* is given, or a bearer personal access
    token (Jira Data Center/Server) when it is not.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        email: str | None = None,
        api_version: int = 2,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("a Jira base URL is required, e.g. https://jira.example.com")
        if not token:
            raise ValueError("an API token or personal access token is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.email = email
        self.api_version = api_version
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #

    def list_keys(self, project_id_or_key: str) -> list[str]:
        """Return every property key stored on *project_id_or_key*."""
        response = self._request("GET", self._url(project_id_or_key))
        data = _decode_json(response) or {}
        return [entry.get("key", "") for entry in data.get("keys", [])]

    def get_value(self, project_id_or_key: str, property_key: str) -> Any:
        """Return the decoded JSON value stored under *property_key*."""
        self._check_key(property_key)
        response = self._request("GET", self._url(project_id_or_key, property_key))
        data = _decode_json(response) or {}
        return data.get("value")

    def set_value(self, project_id_or_key: str, property_key: str, value: Any) -> Any:
        """Create or update a property.

        *value* may be any JSON-serialisable object (dict, list, string,
        number, boolean). Returns the decoded response body when the server
        sends one, otherwise ``None``.
        """
        self._check_key(property_key)
        payload = json.dumps(value)
        _check_size("property value", payload, MAX_VALUE_BYTES)
        response = self._request(
            "PUT", self._url(project_id_or_key, property_key), body=payload
        )
        return _decode_json(response)

    def delete_value(self, project_id_or_key: str, property_key: str) -> None:
        """Delete *property_key* from the project. Fails with 404 if absent."""
        self._check_key(property_key)
        self._request("DELETE", self._url(project_id_or_key, property_key))

    def find_projects_with_property(
        self, property_key: str, *, page_size: int = 100
    ) -> dict[str, Any]:
        """Find every project that has *property_key* set.

        Walks the paginated ``GET /project/search`` endpoint, requesting the
        property alongside each project via the ``properties`` query
        parameter, and returns a ``{project_key: property_value}`` mapping
        for the projects that have the property (sorted by project key).
        Only projects visible to the authenticated user are searched.
        *page_size* is the number of projects per request; Jira allows at
        most 100.
        """
        self._check_key(property_key)
        matches: dict[str, Any] = {}
        start_at = 0
        while True:
            response = self._request(
                "GET",
                self._search_url(),
                params={
                    "startAt": start_at,
                    "maxResults": page_size,
                    "properties": property_key,
                },
            )
            data = _decode_json(response) or {}
            values = data.get("values") or []
            for project in values:
                properties = project.get("properties") or {}
                if property_key in properties:
                    matches[project.get("key")] = properties[property_key]
            if data.get("isLast") or not values:
                break
            start_at += len(values)
        return dict(sorted(matches.items()))

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #

    def _url(self, project_id_or_key: str, property_key: str | None = None) -> str:
        url = "{}/rest/api/{}/project/{}/properties".format(
            self.base_url, self.api_version, quote(str(project_id_or_key), safe="")
        )
        if property_key is not None:
            url += "/" + quote(str(property_key), safe="")
        return url

    def _search_url(self) -> str:
        return "{}/rest/api/{}/project/search".format(self.base_url, self.api_version)

    def _request(
        self,
        method: str,
        url: str,
        body: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        headers = {
            "Accept": "application/json",
            "Authorization": self._authorization_header(),
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = body.encode("utf-8")
        logged_url = url
        if params:
            logged_url = "{}?{}".format(url, urlencode(params, quote_via=quote))
        logger.debug("%s %s", method, logged_url)
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                data=data,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise JiraAPIError(0, "request failed: {}".format(exc)) from exc
        if not response.ok:
            raise _error_from_response(response, auth_hint=self._auth_error_hint())
        return response

    def _authorization_header(self) -> str:
        if self.email:
            # Jira Cloud: API token with the Atlassian account email over
            # HTTP Basic auth.
            credentials = "{}:{}".format(self.email, self.token)
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            return "Basic " + encoded
        # Jira Data Center/Server: personal access token as a bearer token.
        return "Bearer {}".format(self.token)

    def _auth_error_hint(self) -> str:
        if self.email:
            return "check the account email and API token (wrong, expired, or revoked)"
        if ".atlassian.net" in self.base_url:
            return (
                "this looks like a Jira Cloud site, which needs an account"
                " email and API token (HTTP Basic auth) rather than a bearer"
                " token"
            )
        return "check your personal access token (wrong, expired, or missing)"

    @staticmethod
    def _check_key(property_key: str) -> None:
        if not property_key:
            raise ValueError("the property key must not be empty")
        _check_size("property key", property_key, MAX_KEY_BYTES)


# ---------------------------------------------------------------------- #
# helpers                                                                #
# ---------------------------------------------------------------------- #


def _check_size(label: str, text: str, limit: int) -> None:
    size = len(text.encode("utf-8"))
    if size > limit:
        raise ValueError(
            "the {} is {} bytes long, but Jira's limit is {} bytes".format(
                label, size, limit
            )
        )


def _decode_json(response: requests.Response) -> Any:
    """Decode a JSON response body; return ``None`` when there is no body."""
    if not (response.text or "").strip():
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise JiraAPIError(
            response.status_code, "server returned a non-JSON response: {}".format(exc)
        ) from exc


def _error_from_response(
    response: requests.Response, auth_hint: str | None = None
) -> JiraAPIError:
    detail = _extract_error_detail(response)
    message = "HTTP {}".format(response.status_code)
    if detail:
        message += ": {}".format(detail)
    elif response.reason:
        message += " {}".format(response.reason)
    if response.status_code == 401:
        hint = auth_hint
    else:
        hint = _STATUS_HINTS.get(response.status_code)
    if hint:
        message += " ({})".format(hint)
    return JiraAPIError(response.status_code, message)


def _extract_error_detail(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    parts: list[str] = []
    if isinstance(body, dict):
        parts.extend(str(m) for m in (body.get("errorMessages") or []))
        errors = body.get("errors")
        if isinstance(errors, dict):
            parts.extend("{}: {}".format(k, v) for k, v in errors.items())
    if not parts:
        parts.append((response.text or "").strip())
    return "; ".join(part for part in parts if part)[:500]
