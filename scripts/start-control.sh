#!/usr/bin/env bash
set -euo pipefail

# Starts control-plane MCP server + preset executor + UI.
# Assumes you have installed python package in ./python and have ROS2 env available.

export MANASTONE_ENABLE_CONTROL=${MANASTONE_ENABLE_CONTROL:-true}
export MANASTONE_MOTION_CONTROL_TOPIC=${MANASTONE_MOTION_CONTROL_TOPIC:-/mcp_test_loop/preset}
export MANASTONE_PRESET_TOPIC=${MANASTONE_PRESET_TOPIC:-/mcp_test_loop/preset}

# mapping path default points to this repo
export MANASTONE_PRESET_MAPPING=${MANASTONE_PRESET_MAPPING:-python/config/preset_topic_mapping.yaml}

echo "Starting motion-control server (:8087) ..."
mcp-test-loop-motion-control --port 8087 &
PID1=$!

echo "Starting preset executor ..."
mcp-test-loop-executor &
PID2=$!

echo "Starting UI (:7861) ..."
mcp-test-loop-ui &
PID3=$!

trap 'kill $PID1 $PID2 $PID3 2>/dev/null || true' EXIT
wait
