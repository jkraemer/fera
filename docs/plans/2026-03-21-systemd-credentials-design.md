# Design: Systemd Credentials for Secrets (#1426)

## Problem

Secrets are exposed to agent subprocesses via environment variables and hardcoded
config values. The agent can read any secret with `echo $SECRET_NAME` or by
reading config JSON files.

## Solution

Use systemd's `LoadCredential=` to deliver secrets as tmpfs files at service
startup. The gateway reads them into memory immediately, deletes the files, and
never sets them as environment variables. Config JSON files reference secrets via
`${VAR}` placeholders, resolved against an in-memory credentials dict.

## Architecture

### Credential loading

A module-level dict `_credentials` in `config.py` holds all loaded secrets.
`load_all_credentials()` reads every file in `$CREDENTIALS_DIRECTORY`, stores the
contents, then zeros and deletes each file. Called once at the top of each
service's `main()`.

### Variable resolution

`_resolve_var(name)` checks `_credentials` first, then `os.environ`. Both
`substitute_text()` and `substitute_env_vars()` use this instead of
`os.environ.get()` directly.

### Local dev fallback

When `$CREDENTIALS_DIRECTORY` is unset (no systemd), `load_all_credentials()`
returns nothing and `_resolve_var()` falls through to `os.environ.get()`.

## Secret Inventory

| Credential name              | Current state     | Service     |
|------------------------------|-------------------|-------------|
| CLAUDE_CODE_OAUTH_TOKEN      | PassEnvironment   | gateway     |
| ANTHROPIC_API_KEY            | PassEnvironment   | memory      |
| TELEGRAM_BOT_TOKEN           | ${VAR} in config  | main        |
| TELEGRAM_FORGE_BOT_TOKEN     | ${VAR} in config  | coding      |
| TELEGRAM_LORE_BOT_TOKEN      | ${VAR} in config  | librarian   |
| TELEGRAM_ALLOWED_USER_ID     | ${VAR} in config  | all telegram|
| MATTERMOST_MAIN_TOKEN        | hardcoded         | main        |
| MATTERMOST_CODING_TOKEN      | hardcoded         | coding      |
| MATTERMOST_LIBRARIAN_TOKEN   | hardcoded         | librarian   |
| REDMINE_MAIN_API_KEY         | hardcoded         | main        |
| REDMINE_CODING_API_KEY       | hardcoded         | coding      |
| HA_API_TOKEN                 | hardcoded         | main        |

## Code Changes

### config.py

- Add `_credentials: dict[str, str] = {}`
- Add `load_all_credentials()` -- reads $CREDENTIALS_DIRECTORY, populates dict,
  deletes files
- Add `_resolve_var(name)` -- credentials first, then os.environ
- Update `substitute_text()` and `substitute_env_vars()` to use `_resolve_var()`

### gateway/server.py

- Call `load_all_credentials()` at top of `main()`, before config loading

### memory/server.py

- Call `load_all_credentials()` at top of `run_server()`
- Replace `os.environ.get("ANTHROPIC_API_KEY")` with `_resolve_var()`

### Systemd units (gateway, memory, webui)

- Replace `EnvironmentFile=` with `LoadCredential=` lines per secret
- Remove `PassEnvironment=` lines

### Config JSON files (deployed, not in repo)

- Replace hardcoded secrets with `${VAR}` placeholders

## Testing

- Unit: `load_all_credentials()` with temp dir
- Unit: `_resolve_var()` precedence (credentials > env)
- Unit: `substitute_text()` resolves from credentials dict

## Migration (live system)

1. Create `/etc/fera/credentials/` with individual secret files (root:root, 600)
2. Update systemd units with `LoadCredential=` lines
3. Deploy updated code
4. `systemctl daemon-reload && systemctl restart fera-gateway fera-memory`
5. Verify services start, confirm `echo $SECRET` returns empty in agent bash
6. Remove old env files
