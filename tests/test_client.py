"""Unit tests for the REST client (HTTP layer faked out)."""

from __future__ import annotations

import base64
import json

import pytest
import requests

from jira_project_properties.client import (
    MAX_KEY_BYTES,
    MAX_VALUE_BYTES,
    JiraAPIError,
    JiraProjectPropertiesClient,
)

BASE_URL = "https://jira.example.com"


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None):
        self.status_code = status_code
        if text is None:
            text = json.dumps(json_data) if json_data is not None else ""
        self.text = text
        self.ok = 200 <= status_code < 300
        self.reason = "Status {}".format(status_code)

    def json(self):
        if not self.text:
            raise ValueError("no JSON body")
        return json.loads(self.text)


class FakeSession:
    def __init__(self, response=None, responses=None, exception=None):
        if responses is not None:
            self.responses = list(responses)
        elif response is not None:
            self.responses = [response]
        else:
            self.responses = []
        self.exception = exception
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(dict(method=method, url=url, **kwargs))
        if self.exception is not None:
            raise self.exception
        assert self.responses, "no canned response available"
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    @property
    def last(self):
        return self.calls[-1]


def make_client(response=None, responses=None, exception=None, **kwargs):
    session = FakeSession(response=response, responses=responses, exception=exception)
    client = JiraProjectPropertiesClient(
        BASE_URL, "secret-token", session=session, **kwargs
    )
    return client, session


# --------------------------------------------------------------------------- #
# happy paths                                                                  #
# --------------------------------------------------------------------------- #


def test_get_value_returns_decoded_value():
    client, session = make_client(
        response=FakeResponse(json_data={"key": "k", "value": {"a": [1, 2]}})
    )
    assert client.get_value("ABC", "k") == {"a": [1, 2]}
    assert session.last["method"] == "GET"
    assert session.last["url"] == BASE_URL + "/rest/api/2/project/ABC/properties/k"


def test_list_keys_returns_keys_in_order():
    client, _ = make_client(
        response=FakeResponse(
            json_data={
                "keys": [
                    {"key": "one", "self": "..."},
                    {"key": "two", "self": "..."},
                ]
            }
        )
    )
    assert client.list_keys(12345) == ["one", "two"]


def test_requests_carry_bearer_token():
    client, session = make_client(response=FakeResponse(json_data={"keys": []}))
    client.list_keys("ABC")
    headers = session.last["headers"]
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Accept"] == "application/json"


def test_cloud_email_uses_basic_auth():
    client, session = make_client(
        email="user@example.com", response=FakeResponse(json_data={"keys": []})
    )
    client.list_keys("ABC")
    expected = "Basic " + base64.b64encode(
        b"user@example.com:secret-token"
    ).decode("ascii")
    assert session.last["headers"]["Authorization"] == expected


def test_project_and_key_are_url_quoted():
    client, session = make_client(response=FakeResponse(json_data={"value": None}))
    client.get_value("my proj", "a key/with chars")
    assert session.last["url"].endswith(
        "/project/my%20proj/properties/a%20key%2Fwith%20chars"
    )


def test_set_value_sends_put_with_json_body():
    client, session = make_client(response=FakeResponse(json_data={"a": 1}))
    result = client.set_value("ABC", "k", {"a": 1})
    assert result == {"a": 1}
    assert session.last["method"] == "PUT"
    assert session.last["data"] == b'{"a": 1}'
    assert session.last["headers"]["Content-Type"] == "application/json"


def test_set_value_created_with_empty_body_returns_none():
    client, _ = make_client(response=FakeResponse(status_code=201))
    assert client.set_value("ABC", "k", [1, 2, 3]) is None


def test_delete_value_sends_delete():
    client, session = make_client(response=FakeResponse(status_code=204))
    client.delete_value("ABC", "k")
    assert session.last["method"] == "DELETE"


# --------------------------------------------------------------------------- #
# error handling                                                               #
# --------------------------------------------------------------------------- #


def test_api_error_includes_server_detail():
    client, _ = make_client(
        response=FakeResponse(
            status_code=404,
            json_data={"errorMessages": ["The property does not exist."]},
        )
    )
    with pytest.raises(JiraAPIError) as excinfo:
        client.get_value("ABC", "missing")
    assert excinfo.value.status_code == 404
    assert "The property does not exist." in excinfo.value.message
    assert "project id" in excinfo.value.message  # the 404 hint


def test_unauthorized_error_mentions_token():
    client, _ = make_client(response=FakeResponse(status_code=401, json_data={}))
    with pytest.raises(JiraAPIError) as excinfo:
        client.list_keys("ABC")
    assert "token" in excinfo.value.message


def test_unauthorized_hint_for_cloud_mentions_email_and_api_token():
    client, _ = make_client(
        email="user@example.com", response=FakeResponse(status_code=401, json_data={})
    )
    with pytest.raises(JiraAPIError) as excinfo:
        client.list_keys("ABC")
    assert "email" in excinfo.value.message
    assert "API token" in excinfo.value.message


def test_unauthorized_hint_for_cloud_site_without_email():
    session = FakeSession(response=FakeResponse(status_code=401, json_data={}))
    client = JiraProjectPropertiesClient(
        "https://example.atlassian.net", "tok", session=session
    )
    with pytest.raises(JiraAPIError) as excinfo:
        client.list_keys("ABC")
    assert "Jira Cloud" in excinfo.value.message


def test_field_errors_are_included():
    client, _ = make_client(
        response=FakeResponse(
            status_code=400, json_data={"errors": {"value": "must be valid JSON"}}
        )
    )
    with pytest.raises(JiraAPIError) as excinfo:
        client.set_value("ABC", "k", {"x": 1})
    assert "value: must be valid JSON" in excinfo.value.message


def test_transport_failure_raises_with_status_zero():
    client, _ = make_client(exception=requests.ConnectionError("refused"))
    with pytest.raises(JiraAPIError) as excinfo:
        client.list_keys("ABC")
    assert excinfo.value.status_code == 0


# --------------------------------------------------------------------------- #
# client-side validation                                                       #
# --------------------------------------------------------------------------- #


def test_oversized_key_is_rejected_before_request():
    client, session = make_client()
    with pytest.raises(ValueError):
        client.set_value("ABC", "k" * (MAX_KEY_BYTES + 1), {"x": 1})
    assert session.calls == []


def test_oversized_value_is_rejected_before_request():
    client, session = make_client()
    with pytest.raises(ValueError):
        client.set_value("ABC", "k", {"pad": "x" * MAX_VALUE_BYTES})
    assert session.calls == []


def test_empty_key_is_rejected():
    client, _ = make_client()
    with pytest.raises(ValueError):
        client.get_value("ABC", "")


def test_missing_url_or_token_is_rejected():
    with pytest.raises(ValueError):
        JiraProjectPropertiesClient("", "token")
    with pytest.raises(ValueError):
        JiraProjectPropertiesClient(BASE_URL, "")


def test_trailing_slash_in_base_url_is_normalised():
    client = JiraProjectPropertiesClient(BASE_URL + "/", "t")
    assert client.base_url == BASE_URL


def test_api_version_is_used_in_urls():
    client, session = make_client(
        response=FakeResponse(json_data={"keys": []}), api_version=3
    )
    client.list_keys("ABC")
    assert "/rest/api/3/" in session.last["url"]


# --------------------------------------------------------------------------- #
# find_projects_with_property                                                  #
# --------------------------------------------------------------------------- #


def _search_page(values, is_last, total):
    return FakeResponse(
        json_data={
            "startAt": 0,
            "maxResults": len(values) or 1,
            "total": total,
            "isLast": is_last,
            "values": values,
        }
    )


def test_find_projects_collects_matches_from_all_pages():
    pages = [
        _search_page(
            [
                {"key": "ABC", "properties": {"flag": {"on": True}}},
                {"key": "DEF", "properties": {}},
            ],
            is_last=False,
            total=4,
        ),
        _search_page(
            [
                {"key": "GHI"},  # no properties map at all
                {"key": "XYZ", "properties": {"flag": "yes", "other": 1}},
            ],
            is_last=True,
            total=4,
        ),
    ]
    client, session = make_client(responses=pages)
    result = client.find_projects_with_property("flag", page_size=2)
    assert result == {"ABC": {"on": True}, "XYZ": "yes"}
    assert len(session.calls) == 2
    for call in session.calls:
        assert call["url"].endswith("/rest/api/2/project/search")
    assert session.calls[0]["params"] == {
        "startAt": 0,
        "maxResults": 2,
        "properties": "flag",
    }
    assert session.calls[1]["params"]["startAt"] == 2


def test_find_projects_returns_empty_mapping_when_nothing_matches():
    client, _ = make_client(
        response=_search_page([{"key": "ABC", "properties": {}}], True, 1)
    )
    assert client.find_projects_with_property("flag") == {}


def test_find_projects_stops_on_empty_page_without_is_last():
    pages = [
        _search_page([{"key": "ABC", "properties": {"flag": 1}}], False, 5),
        _search_page([], False, 5),
    ]
    client, session = make_client(responses=pages)
    assert client.find_projects_with_property("flag") == {"ABC": 1}
    assert len(session.calls) == 2


def test_find_projects_validates_the_property_key():
    client, session = make_client()
    with pytest.raises(ValueError):
        client.find_projects_with_property("k" * (MAX_KEY_BYTES + 1))
    assert session.calls == []


def test_find_projects_results_are_sorted_by_project_key():
    page = _search_page(
        [
            {"key": "ZZZ", "properties": {"flag": 1}},
            {"key": "AAA", "properties": {"flag": 2}},
        ],
        True,
        2,
    )
    client, _ = make_client(response=page)
    assert list(client.find_projects_with_property("flag")) == ["AAA", "ZZZ"]
