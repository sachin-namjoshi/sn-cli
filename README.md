# `sn-cli` — a command-line tool for ServiceNow

> **Not the official ServiceNow CLI.**
> ServiceNow ships its own CLI out-of-the-box ([ServiceNow CLI docs](https://www.servicenow.com/docs/r/application-development/servicenow-cli/servicenow-cli.html)) for application development workflows (syncing files, deploying apps, etc.).
> `sn-cli` (`snow`) is an independent, lightweight alternative focused on querying tables, updating records, and managing Service Portal widgets from your terminal — with browser-based OAuth login and zero dependencies.

`snow` is a command-line tool for working with ServiceNow from your terminal.
You can query tables, inspect the data dictionary, and build Service Portal
widgets — including rendering a widget server-side to see its real output and
errors. It logs you in through your browser (Okta/MFA or any configured IdP),
so every action is attributed to you.

It's a single-file Python tool with **no dependencies to install** beyond
Python 3.12, and it's designed to be driven by Claude Code — but every command
also works on its own.

## Requirements

- Python 3.12+
- An OS keychain for storing your login (macOS Keychain, Windows Credential
  Manager, or Linux Secret Service — `secret-tool`). The tool uses this
  automatically; on Linux you may need `apt install libsecret-tools gnome-keyring`.

## Install

```
pip install .        # or: pipx install .   → puts `snow` on your PATH
```

Until you install it, run `./snow` from the repo root.

## Configure

Create `~/.snow/config.json` (and `chmod 600` it):

```json
{
  "instance": "your-instance.service-now.com",
  "client_id": "<oauth client id>",
  "redirect_uri": "http://localhost:49160/callback",
  "dev_instances": ["your-instance-dev", "your-instance-dev2"]
}
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `instance` | yes | Your ServiceNow instance hostname |
| `client_id` | yes | OAuth app Client ID (registered in ServiceNow) |
| `redirect_uri` | yes | Must match the OAuth app's redirect URI |
| `dev_instances` | no | Short instance names allowed to run DEV commands (widget, updateset, dict). Omit to block dev commands everywhere. |
| `initials` | no | Initials for update-set names (e.g. `"JB"`). Derived from your user record if omitted. |

There's no client secret to add — the app signs you in using PKCE (no secret
to distribute).

### Registering an OAuth app

In your ServiceNow instance, go to **System OAuth → Application Registry → New
→ Create an OAuth API endpoint for external clients**:

- Redirect URL: `http://localhost:49160/callback`
- Leave client secret blank (PKCE flow)

Copy the generated Client ID into your config.

## Log in

```
snow auth login      # opens your browser → IdP/MFA → you're done
snow auth status     # shows who you're signed in as and when the token expires
```

Your tokens are stored in your OS keychain, never in a file. Refresh happens
automatically; you won't need to log in again until the refresh token expires
(~100 days).

The login popup is your instance's configured identity provider (Okta, Azure
AD, SAML, or local auth) — the CLI just opens the browser and waits.

## Commands

| Command | What it does |
| --- | --- |
| `snow auth login` | Sign in through your browser. |
| `snow auth refresh` | Refresh your session manually (usually automatic). |
| `snow auth status` | Show your identity and token expiry. |
| `snow query <table> [-q <query>] [-f <fields>] [-l <limit>] [--json]` | Read records from any table. |
| `snow update <table> <sys_id\|> [-q <query>] --set field=value ... [--yes] [--json]` | Update fields on a record. |
| `snow dict <table> [--own] [--json]` | List a table's fields, types, and references (`--own` = this table only, not inherited). |
| `snow widget create --id <id> --name "<Name>"` | Create a new widget (scaffolds the five source files). |
| `snow widget pull <name\|id\|sys_id>` | Download a widget's source into a local folder. |
| `snow widget push [--yes] [--force]` | Upload your local changes (shows a diff and asks first). |
| `snow widget render [<id>] [--json]` | Render a widget server-side and show its live output and any errors. |
| `snow updateset list` | List your in-progress update sets (`*` = current). |
| `snow updateset new "<feature>"` | Create and switch to a new update set for a feature. |
| `snow updateset use <sys_id>` | Switch to an existing update set. |

## Working with widgets

`snow widget pull` writes a widget's source into a folder named after the
widget:

```
inventory-reservation-manager/
├── template.html  css.scss  client_script.js  server_script.js  option_schema.json
└── .snow-meta.json     # tracks the widget's identity and update set
```

Edit the files, then `snow widget push`. Push shows you exactly which fields
changed and asks for confirmation before writing. If someone else edited the
widget on the server since you pulled, push stops and asks you to re-pull (or
pass `--force` to overwrite anyway).

**See a widget's real output with `snow widget render`.** It runs the widget's
server script on the instance and shows you the resulting data — so you can
catch both thrown errors (with the message, line, and field) and silent bugs
(where it renders but the values are wrong). This is server-side only;
client-side/visual behavior isn't covered.

Changes are captured into your current update set. Start a feature with
`snow updateset new "add logging"` and your pushes are grouped under a set named
`{Initials}: {Widget Name} - {Feature}`.

## Which instances you can use

To prevent accidents, some commands only run against instances you explicitly
mark as development in `config.json`:

- **Anywhere:** `auth`, `query` — signing in and reading data work on any instance.
- **Dev instances only:** `widget`, `updateset`, `dict` — refused on any instance
  not listed in `dev_instances` (exit code 7) before any network call.

This is enforced in code, not just config — the `dev_instances` list controls
*where* dev commands run, but the policy that restricts them is in source.

## Good to know

- Your login lives in your OS keychain; `~/.snow/config.json` holds only
  non-secret app identifiers.
- All connections are HTTPS-only, and output is scrubbed of tokens before it's
  printed.
- Works with any ServiceNow IdP: Okta, Azure AD, PingFederate, SAML, local auth
  — the CLI opens your browser and waits; the IdP is transparent to it.

## Tests

```
python3 -m unittest discover -s tests
```

Runs fully offline — no network calls, no writes to a real account.
