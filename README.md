# jira-project-properties

`jira-props` is a small command line tool for managing **project entity
properties** in Jira — arbitrary JSON key/value pairs stored against a project
through the official REST API:

- `list` — every property key on a project (one per line)
- `get` — the value of one property (pretty-printed JSON)
- `set` — create or update a property
- `delete` — remove a property
- `find` — every project that has a given property, as a `{project: value}` JSON object

API reference:
<https://developer.atlassian.com/cloud/jira/platform/rest/v2/api-group-project-properties/>

## Requirements

- Python 3.9+
- [uv](https://docs.astral.sh/uv/) (recommended) or plain pip

## Setup

With uv (creates the `.venv` virtual environment and installs everything):

```bash
uv sync
uv run jira-props --help
```

Without uv:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/jira-props --help
```

## Configuration

`jira-props` supports both of Jira's token authentication methods:

- **Jira Cloud** — an Atlassian account **email** plus an **API token**, sent
  as HTTP Basic auth. Create an API token at
  <https://id.atlassian.com/manage-profile/security/api-tokens/> (see
  [Manage API tokens for your Atlassian account](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)).
- **Jira Data Center / Server** — a **personal access token (PAT)** sent as
  `Authorization: Bearer` (Profile → Personal Access Tokens; see
  [Atlassian's PAT docs](https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html)).

The rule is simple: **provide an email and the token is used as a Cloud API
token (Basic auth); provide only a token and it is sent as a bearer PAT.**

| Setting               | Flag                                    | Environment variable                          |
| --------------------- | --------------------------------------- | --------------------------------------------- |
| Jira base URL         | `--url https://your-domain.atlassian.net` | `JIRA_URL`                                  |
| Account email (Cloud) | `--email you@example.com`              | `JIRA_EMAIL`                                  |
| API token / PAT       | `--token <token>`                      | `JIRA_API_TOKEN`, `JIRA_TOKEN`, or `JIRA_PAT` |

Flags take precedence over environment variables; the token variables are
checked in the order listed.

### Jira Cloud

```bash
export JIRA_URL=https://your-domain.atlassian.net
export JIRA_EMAIL=you@example.com
export JIRA_API_TOKEN=your-api-token

jira-props list ABC
```

### Jira Data Center / Server

```bash
export JIRA_URL=https://jira.example.com
export JIRA_PAT=your-personal-access-token

jira-props list ABC
```

## Usage

```bash
# List all property keys of a project (accepts a project key or numeric id)
jira-props list ABC

# Get the value of one property
jira-props get ABC deployment.notes

# Find every project that has a property, with its value
jira-props find deployment.notes

# Print each API request (method and endpoint) to stderr while running
jira-props --debug find deployment.notes

# Set a property (the value must be valid JSON)
jira-props set ABC deployment.notes --value '{"environment": "prod", "replicas": 3}'

# Set a property from a file (or pipe JSON on stdin: cat f.json | jira-props set ...)
jira-props set ABC deployment.config --file config.json

# Store a plain string (note the extra quotes)
jira-props set ABC owner --value '"platform-team"'

# Delete a property
jira-props delete ABC deployment.notes

# Everything in one call, no environment variables needed
jira-props --url https://your-domain.atlassian.net --email you@example.com \
    --token "$JIRA_API_TOKEN" list ABC
```

## Exit codes

| Code | Meaning                                                       |
| ---- | ------------------------------------------------------------- |
| 0    | success                                                       |
| 1    | Jira returned an error (bad credentials, 404, 403, ...) or the request failed |
| 2    | usage or configuration error (missing JSON, no URL, ...)      |

## Notes and limits

- Property keys are limited to 255 bytes and values to 32,768 bytes; both are
  checked client-side before sending.
- `find` walks the paginated `GET /project/search` endpoint and uses its
  `properties` query parameter, so each project's value arrives with the
  search results — one request per 50 projects. Results are sorted by
  project key and only include projects visible to the authenticated user.
- `--debug` prints one line per API call (`METHOD endpoint`, query string
  included) to stderr, so stdout stays pipeable; combine it with `find` to
  watch pagination advance.
- Reading requires the *Browse Projects* permission; setting/deleting requires
  *Administer Projects* (or global *Administer Jira*).
- Jira Cloud serves the same endpoints under both `/rest/api/2/` and
  `/rest/api/3/`; use `--api-version 3` if you prefer the newer one.
- If you point the tool at an `*.atlassian.net` site without an email, it
  warns you that Cloud expects email + API token (Basic) rather than a
  bearer token.

## Development

```bash
uv run pytest          # run the test suite
```

The tests include an end-to-end suite that runs the client against a local
mock of the Jira API. The same mock is available as a standalone server for
manual experiments:

```bash
uv run python scripts/mock_jira_server.py --port 8923
uv run jira-props --url http://127.0.0.1:8923 --email demo@example.com --token demo list ABC
```
