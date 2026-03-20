#!/usr/bin/env bash
set -euo pipefail

echo "=== Fera install ==="

# System packages
echo "Installing system packages..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv curl git ca-certificates

# uv
if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Python dependencies
echo "Installing Python dependencies..."
uv sync

# Node.js (for Claude Code CLI)
if ! command -v node &>/dev/null; then
    echo "Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# Claude Code CLI
if ! command -v claude &>/dev/null; then
    echo "Installing Claude Code CLI..."
    sudo npm install -g @anthropic-ai/claude-code
fi

echo ""
echo "=== Fera installed ==="
echo "Next steps:"
echo "  claude login    # authenticate with Claude (Max subscription or API key)"
echo "  uv run fera"
