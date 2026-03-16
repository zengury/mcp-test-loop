#!/usr/bin/env bash
set -euo pipefail

# Starts vendored manastone observation MCP servers in mock mode.

cd "$(dirname "$0")/../python"

export MANASTONE_MOCK_MODE=true
export MANASTONE_ROBOT_ID=${MANASTONE_ROBOT_ID:-robot_01}

# Tip: edit config/servers.yaml to enable/disable servers.
exec manastone-launcher
