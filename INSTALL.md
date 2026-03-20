# Fera Server — Manual Setup Guide

This document describes how to set up a fera server from scratch on a fresh
Debian Trixie install. The fera server is an AI agent platform that runs
several cooperating services in a service account's home directory.

**Starting point:** Fresh Debian Trixie, root access, network configured.

---

## 1. System Packages

```bash
apt update
apt install -y \
  python3 python3-venv \
  curl git ca-certificates \
  nodejs npm \
  poppler-utils tesseract-ocr pandoc catdoc \
  yt-dlp
```

---

## 2. Create the `fera` User

```bash
useradd --shell /bin/bash --create-home --groups systemd-journal fera
```

---

## 3. SSH Authorized Keys

For easy access and deployment, set up passwordless ssh access for the `fera` user.

~~~bash
ssh-copy-id -i /path/to/your/key.pub fera@FERA_HOST_IP
~~~

---

## 4. Install uv (Python Package Manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cp ~/.local/bin/uv /usr/local/bin/uv
```

---

## 5. Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

Verify: `claude --version`

---

## 6. Install the Fera Application

From a local checkout of the repository, deploy to the server with:

```bash
DEPLOY_HOST=your-server.example.com make deploy
```

This command (defined in `Makefile`) does the following over SSH as root:

- Archives the current git HEAD and unpacks it to `/opt/fera-new`
- Builds the web UI frontend (`npm ci && npm run build`)
- Copies systemd service files to `/etc/systemd/system/`
- Syncs the Python virtualenv at `/opt/fera-venv` (using uv)
- Atomically swaps `/opt/fera-new` → `/opt/fera` (keeping the previous version as `/opt/fera.bak`)
- Restarts all fera services

The server must have `uv`, `npm`, and `node` installed (steps 4–5 above).
SSH access as root is required from the machine running `make deploy`.

While you could simply deploy as the fera user and run from somewhere in
`/home/fera`, I chose to not do this in order to prevent direct self-modification. I do let
fera commit changes to it's own code base (in a separate working directory
somewhere under `/home/fera/`), but lacking root access it cannot 'make install'
and update itself - this requires me doing `make deploy` from my machine.

---

## 7. Environment Files

Run `claude setup-token` locally to generate a long-lived OAuth token to be used
by the the Agent SDK.

Create one or more telegram bots, you'll want to have one for each agent. Just
define more env variables and reference them in the fera config where the agents
are declared.

```bash
install -d -m 700 /etc/fera


# fera-gateway.env — required: CLAUDE_CODE_OAUTH_TOKEN
install -m 600 /dev/stdin /etc/fera/fera-gateway.env <<'EOF'
CLAUDE_CODE_OAUTH_TOKEN=<your Claude Code OAuth token>
# Optional Telegram integration:
#TELEGRAM_BOT_TOKEN=<token>
#TELEGRAM_ALLOWED_USER_ID=<your Telegram user ID>
EOF


# fera-memory.env — set ANTHROPIC_API_KEY to enable deep memory search
# (uses Haiku for query expansion only, so running costs are negligible)
install -m 600 /dev/stdin /etc/fera/fera-memory.env <<'EOF'
#ANTHROPIC_API_KEY=<your Anthropic API key>
EOF
```

---

## 8. Systemd Service Files

Create the three core service units, then enable them:

**`/etc/systemd/system/fera-memory.service`**

```ini
[Unit]
Description=Fera Memory Server
After=network.target

[Service]
Type=simple
User=fera
Environment=FERA_HOME=/home/fera
EnvironmentFile=-/etc/fera/fera-memory.env
PassEnvironment=ANTHROPIC_API_KEY
ExecStart=/opt/fera-venv/bin/fera-memory-server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/fera-gateway.service`**

```ini
[Unit]
Description=Fera Gateway
After=network.target fera-memory.service
Requires=fera-memory.service

[Service]
Type=simple
User=fera
Environment=FERA_HOME=/home/fera
EnvironmentFile=-/etc/fera/fera-gateway.env
PassEnvironment=CLAUDE_CODE_OAUTH_TOKEN
ExecStart=/opt/fera-venv/bin/fera-gateway
Restart=on-failure
RestartSec=5
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/fera-webui.service`**

```ini
[Unit]
Description=Fera Web UI
After=network.target fera-gateway.service

[Service]
Type=simple
User=fera
Environment=FERA_HOME=/home/fera
EnvironmentFile=-/etc/fera/fera-webui.env
ExecStart=/opt/fera-venv/bin/fera-webui
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable everything:

```bash
systemctl daemon-reload
systemctl enable --now fera-memory.service
systemctl enable --now fera-gateway.service
systemctl enable --now fera-webui.service
```

---

## 9. Configure Fera and Create Your First Agent

At this point the services are running but there's no agent yet. The gateway
will start but has nothing to talk to until you create an agent workspace.

### Create the agent workspace

```bash
runuser -u fera -- /opt/fera-venv/bin/fera-create-agent main
```

This initialises `$FERA_HOME/agents/main/workspace/` from the built-in
templates — AGENTS.md, MEMORY.md, persona files, and the directory structure
the agent expects.

### Write config files

The gateway works without any config files (all settings have defaults), but
you'll want to wire up at least your Telegram adapter and the heartbeat.

Config is split across two files:

- **`$FERA_HOME/config.json`** — global settings (heartbeat, gateway, MCP servers)
- **`$FERA_HOME/agents/main/config.json`** — per-agent settings (adapters, tool allowlists)

**Global config** — enable heartbeat:

```bash
install -o fera -g fera -m 600 /dev/stdin /home/fera/config.json <<'EOF'
{
  "heartbeat": {
    "enabled": true,
    "interval_minutes": 30,
    "active_hours": "08:00-22:00"
  }
}
EOF
```

**Agent config** — wire up the Telegram adapter:

```bash
install -o fera -g fera -m 600 /dev/stdin /home/fera/agents/main/config.json <<'EOF'
{
  "adapters": {
    "telegram": {
      "bot_token": "${TELEGRAM_BOT_TOKEN}",
      "allowed_users": [YOUR_TELEGRAM_USER_ID],
      "default_session": "default"
    }
  }
}
EOF
```

Replace `YOUR_TELEGRAM_USER_ID` with your numeric Telegram user ID (you can
get it from [@userinfobot](https://t.me/userinfobot)).

The `TELEGRAM_BOT_TOKEN` environment variable is already defined in
`/etc/fera/fera-gateway.env` from step 7 — no duplication needed.

For the full list of config keys (MCP servers, per-agent overrides, web UI
options, etc.) see the **Configuration** section in
[README.md](https://github.com/jkraemer/fera#configuration).

### Restart the gateway

```bash
systemctl restart fera-gateway
```

The gateway auto-initialises any agent workspaces declared in the config on
startup, but restarting ensures it picks up the new `config.json`.

### Verify

Open the web UI at `http://<host>:8080` — you should see the `main` agent
listed with a session available. Send a message; the agent should respond.

---

## 10. Tailscale

Fera connects to the infrastructure's private network via a self-hosted
Headscale server.

```bash
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg \
  -o /usr/share/keyrings/tailscale-archive-keyring.gpg

cat > /etc/apt/sources.list.d/tailscale.sources <<'EOF'
Types: deb
URIs: https://pkgs.tailscale.com/stable/debian
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/tailscale-archive-keyring.gpg
EOF

apt update && apt install -y tailscale
systemctl enable --now tailscaled

# Join the network
tailscale up # --login-server https://your.headscale.server

# Disable Tailscale's DNS override (we use system DNS)
tailscale set --accept-dns=false
```

---

## Ports

| Port | Service | Bind Address | Protocol | Notes |
|------|---------|--------------|----------|-------|
| 8080 | fera-webui | 0.0.0.0 | HTTP | Web UI |
| 8389 | fera-gateway | 127.0.0.1 | WebSocket | Main gateway |
| 8390 | fera-memory-server | 127.0.0.1 | HTTP (SSE/MCP) | Internal only |

The gateway and memory server listen on localhost by default. The web UI
binds to `0.0.0.0`. For remote access to the gateway, set `gateway.host` to
`"0.0.0.0"` in `$FERA_HOME/config.json`.

## Firewall

Allow inbound TCP 8080 (web UI) from trusted clients only. If the gateway
is exposed (host set to `0.0.0.0`), also allow TCP 8389. Block port 8390
from external access (it binds to localhost by default, but an extra firewall
rule adds defence in depth).

---

## Day-to-Day Operations

```bash
# Service status
systemctl status fera-memory fera-gateway fera-webui

# Logs
journalctl -u fera-gateway -f
journalctl -u fera-memory -f
```

## Updating

From a local checkout:

```bash
DEPLOY_HOST=your-server.example.com make deploy
```

The previous version is kept at `/opt/fera.bak` automatically.

## Rolling Back

If something goes wrong after a deploy:

```bash
DEPLOY_HOST=your-server.example.com make rollback
```

This swaps `/opt/fera.bak` back into place, re-syncs the venv, and restarts services. Only one backup is kept — a second deploy overwrites `/opt/fera.bak`.

---

## Secrets Reference

| Secret | Where used | Notes |
|--------|-----------|-------|
| `CLAUDE_CODE_OAUTH_TOKEN` | `/etc/fera/fera-gateway.env` | Required |
| `ANTHROPIC_API_KEY` | `/etc/fera/fera-memory.env` | Optional (enables deep memory search) |
| `TELEGRAM_BOT_TOKEN` | `/etc/fera/fera-gateway.env` | Optional |
| `TELEGRAM_ALLOWED_USER_ID` | `/etc/fera/fera-gateway.env` | Optional |

---

## Optional Add-ons

### Knowledge Base

The knowledge base gives your agent searchable access to a collection of
documents — PDFs, text files, markdown notes, images (via OCR). It's a
two-stage pipeline: a daemon extracts and chunks documents into a staging
area, then an ingest step writes them as memory files for a dedicated
librarian agent to search. Not needed for the core agent to function.

#### How it works

```
~/knowledge/                     ← source documents (e.g. Syncthing folder)
    │
    │  watches (inotify via watchdog)
    ▼
fera-knowledge-indexer (daemon)
    │  extracts text, chunks, writes staging files
    ▼
~/agents/librarian/knowledge/    ← staging area
    ├── metadata.json
    ├── content/                 ← extracted text chunks
    ├── state.json               ← indexer bookkeeping
    └── deletions.jsonl          ← tracks source file removals
    │
    │  fera-knowledge-ingest (periodic CLI)
    ▼
~/agents/librarian/workspace/memory/knowledge/
    └── <doc-id>/chunk1.md ...   ← memory files with YAML front-matter
    │
    │  memory_search (MCP)
    ▼
Librarian agent ("Lore")         ← Haiku-powered, answers document queries
```

**Text extraction** supports `.md`, `.txt`, `.pdf` (with a fallback chain:
pypdf → pdftotext → OCR), and images (`.png`, `.jpg`, `.tiff` via
tesseract). Syncthing conflict files are skipped automatically.

**Chunking** splits documents at paragraph boundaries (~8k tokens per chunk
with ~200-token overlap for context continuity).

#### Step 1: Knowledge indexer daemon

The daemon watches the source folder and writes extracted chunks to the
staging area in real time.

**`/etc/systemd/system/fera-knowledge-indexer.service`**

```ini
[Unit]
Description=Fera Knowledge Base Indexer
After=network.target

[Service]
Type=simple
User=fera
ExecStart=/opt/fera-venv/bin/fera-knowledge-indexer \
    /home/fera/knowledge \
    /home/fera/agents/librarian/knowledge
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fera-knowledge-indexer

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now fera-knowledge-indexer.service
```

The first argument is the folder of source documents to watch; the second is
the staging area where extracted content is written. Both must exist inside
`/home/fera` and be readable by the fera user.

#### Step 2: Knowledge ingest

The ingest step reads the staging area, wraps each chunk in a markdown file
with YAML front-matter (source path, hash, chunk number), writes them to the
librarian agent's memory directory, and cleans up the staging files. It also
processes `deletions.jsonl` to remove memory files for deleted source
documents.

Run it manually:

```bash
runuser -u fera -- fera-knowledge-ingest \
    /home/fera/agents/librarian/knowledge \
    /home/fera/agents/librarian/workspace/memory/knowledge
```

Or schedule it via cron (e.g. every 15 minutes):

```bash
# /etc/cron.d/fera-knowledge-ingest
*/15 * * * * fera /opt/fera-venv/bin/fera-knowledge-ingest \
    /home/fera/agents/librarian/knowledge \
    /home/fera/agents/librarian/workspace/memory/knowledge
```

The first argument is the staging area (same as the indexer's output dir);
the second is the librarian agent's memory directory where the indexed
documents end up as searchable `.md` files.

#### Required directories

```bash
runuser -u fera -- mkdir -p \
    /home/fera/knowledge \
    /home/fera/agents/librarian/knowledge \
    /home/fera/agents/librarian/workspace/memory/knowledge
```

#### System dependencies

The indexer uses external tools for extraction. These are already listed in
the system packages step, but for reference:

- `poppler-utils` — provides `pdftotext` and `pdftoppm` (PDF text and image extraction)
- `tesseract-ocr` — OCR for scanned PDFs and images

---

### Playwright

Playwright enables browser automation for the fera agent. Only needed if
your agent workflows involve web scraping or browser control.

```bash
npx playwright install-deps
```

This installs the system-level browser dependencies. To also install the
browser binaries for the fera user:

```bash
runuser -u fera -- npx playwright install
```

---

### Syncthing

Syncthing lets the fera user sync files with other devices. Useful if the
agent needs access to documents or a knowledge base that lives elsewhere.

```bash
apt install -y syncthing
systemctl enable --now syncthing@fera.service
```

On first start, syncthing generates its configuration under
`/home/fera/.config/syncthing/`. Connect to the web UI at
`http://<host>:8384` to configure shared folders and remote devices.

### Himalaya

Fera can read email and save drafts without ever seeing your IMAP credentials.
This is achieved with a small setuid Rust binary (`himalaya-wrapper`) that acts
as a gatekeeper in front of the [himalaya](https://github.com/pimalaya/himalaya)
CLI.

#### How it works

```
fera user  →  himalaya-wrapper (setuid himalaya)  →  himalaya  →  IMAP
              ↳ checks allowlist                    ↳ reads config
              ↳ blocks send/write/reply/forward       (only himalaya user can)
              ↳ clears environment
```

- **Credential isolation:** Himalaya's config (with IMAP password) lives at
  `/home/himalaya/.config/himalaya/config.toml`, owned by the `himalaya` system
  user with mode 600. The `fera` user cannot read it.
- **setuid execution:** `himalaya-wrapper` is owned by `himalaya` with the setuid
  bit set (`-rwsr-x---`). When `fera` executes it, the process runs as `himalaya`
  and can read the config — but `fera` never sees its contents.
- **Allowlist enforcement:** Before exec'ing himalaya, the wrapper checks the
  command/subcommand pair against a hardcoded allowlist. Allowed: `envelope list`,
  `message read`, `message save`, `attachment download`, `flag add/remove`, etc.
  Blocked: `message send/write/reply/forward`, `template send/write/reply/forward`,
  and anything unrecognised. A blocked command exits immediately with an error.
- **Environment clearing:** The wrapper calls `env_clear()` before exec'ing
  himalaya, then sets only `HOME` (to himalaya's home) and a minimal `PATH`.
  This prevents credential injection via environment variables.

The net result: even if the agent is fully compromised, it cannot send email or
extract IMAP credentials — the kernel enforces the separation.

#### Setup

```bash
# 1. Create a dedicated system user for himalaya
useradd --system --shell /usr/sbin/nologin --home-dir /home/himalaya --create-home himalaya

# 2. Create config directory (only himalaya can read it)
install -d -o himalaya -g himalaya -m 700 /home/himalaya/.config/himalaya

# 3. Download himalaya
curl -LsSf https://github.com/pimalaya/himalaya/releases/download/v1.2.0/himalaya.x86_64-linux.tgz \
  | tar -xz -C /usr/local/bin

# 4. Write the config file (credentials stored here, mode 600)
install -o himalaya -g himalaya -m 600 /dev/stdin /home/himalaya/.config/himalaya/config.toml <<'EOF'
[accounts.default]
email = "user@example.com"
default = true

folder.aliases.inbox = "INBOX"
folder.aliases.sent = "Sent"
folder.aliases.drafts = "Drafts"
folder.aliases.trash = "Trash"

backend.type = "imap"
backend.host = "mail.example.com"
backend.port = 993
backend.encryption.type = "tls"
backend.login = "user@example.com"
backend.auth.type = "password"
backend.auth.raw = "<IMAP password>"
EOF

# 5. Build and install himalaya-wrapper (requires Rust toolchain)
make deploy-himalaya
```

Verify the permissions:

```
$ ls -la /usr/local/bin/himalaya-wrapper
-rwsr-x--- 1 himalaya fera 482544 ... himalaya-wrapper
```

The `s` in the owner execute position confirms the setuid bit is set.

---

## Encrypted Home

The production setup encrypts `/home/fera` so that the AI agent's working
data, credentials, and knowledge base are protected at rest. This section
describes how to add encryption on top of the base setup.

### Encryption options

**gocryptfs (directory-level, used in production):** Encrypts individual
files in place. The ciphertext lives at `/srv/fera-encrypted` and the
plaintext is mounted at `/home/fera` on demand. Simple to set up, no
partition changes required.

**LUKS home partition:** Create a dedicated LVM logical volume or partition
for `/home/fera`, format it with LUKS, and mount it at boot (or manually).
Gives full-disk semantics (no filename leakage) at the cost of needing
partition layout planning. Decrypt with `cryptsetup open` and mount before
starting services.

**LUKS root partition:** Encrypt the entire root filesystem at install time
via the Debian installer's guided partitioning. Easiest to reason about but
requires either a remote unlock mechanism (e.g., Dropbear in initrd) or
physical console access at every boot. Overkill for most threat models.

### gocryptfs setup

The consequence of encrypting the home directory is that `/home/fera` doesn't
exist until it is unlocked. This means:

- The `fera` user must be created **without** a home directory.
- All fera services must be **disabled** at boot — they cannot start until
  the home is mounted.
- SSH login as `fera` only works after unlock (this is fine — use root to
  run the unlock script).

#### 1. Re-create the fera user without a home dir

If you followed the base guide and the home dir already exists, back up its
contents first, then:

```bash
usermod --no-create-home fera   # home flag in passwd stays, but dir won't be auto-created again
rm -rf /home/fera               # remove the unencrypted home
mkdir /home/fera                # empty mount point
```

Or if starting fresh, replace the `useradd` in step 2 with:

```bash
useradd --shell /bin/bash --no-create-home --groups systemd-journal fera
mkdir /home/fera   # empty mount point, not owned by fera yet
```

#### 2. Enable FUSE `user_allow_other`

This allows root (and systemd) to access the FUSE mount created by the fera
user:

```bash
apt install -y gocryptfs
sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
```

> **Proxmox LXC:** gocryptfs requires FUSE, which is disabled by default in
> LXC containers. Enable it on the host before proceeding:
> `pct set <vmid> --features fuse=1`

#### 3. Initialize and mount the encrypted volume

```bash
# Cipher directory — owned by fera, never directly accessed
install -d -o fera -g fera -m 700 /srv/fera-encrypted

# Initialize (will prompt for a passphrase — keep it safe, data is unrecoverable without it)
runuser -u fera -- gocryptfs -init /srv/fera-encrypted

# Mount with -allow_root so root and systemd can access the plaintext
runuser -u fera -- gocryptfs -allow_root /srv/fera-encrypted /home/fera

# Set up home dir structure inside the encrypted volume
chown fera:fera /home/fera
chmod 700 /home/fera
runuser -u fera -- mkdir -p /home/fera/.ssh /home/fera/knowledge /home/fera/agents/librarian/knowledge
```

Now place the SSH authorized keys as described in the base guide — everything
written to `/home/fera` is encrypted transparently.

#### 4. Disable services at boot

```bash
systemctl disable fera-memory.service fera-gateway.service fera-webui.service
# If optional components are installed:
# systemctl disable fera-knowledge-indexer.service
# systemctl disable syncthing@fera.service
```

#### 5. Install the unlock script

```bash
install -d -m 700 /root/bin

install -m 700 /dev/stdin /root/bin/unlock-knowledge <<'EOF'
#!/bin/bash
set -euo pipefail

CIPHER_DIR=/srv/fera-encrypted
MOUNT_DIR=/home/fera

if mountpoint -q "$MOUNT_DIR"; then
    echo "$MOUNT_DIR is already mounted."
    exit 1
fi

echo "Mounting encrypted home volume..."
runuser -u fera -- gocryptfs -allow_root "$CIPHER_DIR" "$MOUNT_DIR"

echo "Starting fera services..."
systemctl start fera-memory.service
systemctl start fera-gateway.service
systemctl start fera-webui.service
# Uncomment optional components if installed:
# systemctl start fera-knowledge-indexer.service
# systemctl start syncthing@fera.service

echo "Done."
EOF
```

#### After reboot

```bash
# SSH in as root, then:
/root/bin/unlock-knowledge
# Enter gocryptfs passphrase — all services start automatically
```

To shut down cleanly:

```bash
systemctl stop fera-memory fera-gateway fera-webui
# systemctl stop fera-knowledge-indexer syncthing@fera  # if installed
umount /home/fera
```
