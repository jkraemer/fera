# Fera Design: Authentication

How clients authenticate with the Fera gateway.

---

## 1. Token Authentication

The gateway uses a pre-shared token for WebSocket authentication.

### Token Generation
On first run, `ensure_auth_token()` in `src/fera/config.py` generates a cryptographically strong token via `secrets.token_hex(32)` (256 bits) and saves it to `$FERA_HOME/config.json` under `gateway.auth_token`. The token is logged on startup (first and last 4 characters visible).

### Handshake Flow
1. Client opens a WebSocket connection to the gateway.
2. An auth timeout timer starts (default: 5 seconds). If no valid auth arrives in time, the connection is closed with code 1008.
3. Client sends a `connect` request with `{"token": "..."}`.
4. Server verifies the token using `hmac.compare_digest()` (constant-time comparison to prevent timing attacks).
5. On success: connection is added to the `_authenticated` set, the timeout timer is cancelled, and the server responds with a `hello-ok` payload containing sessions, agents, and version.
6. On failure: server responds with `"Authentication failed"` and the connection remains unauthenticated.

All non-`connect` requests from unauthenticated connections are rejected.

### Optional Auth
If no `auth_token` is configured, authentication is skipped entirely — all connections are trusted.

---

## 2. Network Security

The gateway binds to `127.0.0.1:8389` by default, making it unreachable from outside the host. The web UI (port 8080) proxies WebSocket connections to the gateway on localhost.

For remote access, the deployment guide (`INSTALL.md`) recommends Tailscale, which provides encrypted tunnels without exposing the gateway directly.

---

## 3. Configuration

In `$FERA_HOME/config.json`:

```json
{
  "gateway": {
    "host": "127.0.0.1",
    "port": 8389
  }
}
```

The `auth_token` field is auto-generated on first run. The `auth_timeout` is configurable (default: 5 seconds) as a constructor parameter.

---

## 4. Not Implemented

The following items from earlier design thinking are **not** in the codebase:

- **Tailscale identity auth (zero-config):** No `whois` API lookup or `Tailscale-User-Login` header parsing. Tailscale is used only as a transport layer.
- **Rate limiting / exponential backoff:** No per-IP failure tracking or automatic blocking. (Client-side reconnection backoff exists in `gateway/client.py` but that's for reconnection, not auth failures.)
- **Explicit state machine:** Connection states (UNKNOWN → CHALLENGE → AUTHENTICATED → CLOSED) are implicit via the `_authenticated` set and timeout task, not modeled as an enum.
- **WSS enforcement:** The gateway accepts plain `ws://`. Encryption is delegated to the deployment layer (Tailscale or reverse proxy).
