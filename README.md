# mcp-test-loop

Minimal long-horizon **test loop agent** for ROS2 robots using:

- **MCP servers (SSE transport)** for observation + control
- A minimal **LoopForever runtime** (based on `@mariozechner/pi-agent-core` agent-loop)
- Operator-friendly **Gradio UI** for configuring presets + mappings + viewing loop logs

## Architecture (minimal)

1) **Loop runtime** (Node.js) runs ticks forever:
- deterministically calls observation tools in strict order
- asks LLM for a single JSON decision
- optionally calls a control MCP tool to execute a preset

2) **motion-control MCP server** (Python) exposes:
- `list_presets()`
- `execute_preset(preset, dry_run)`

It publishes the preset name to a ROS2 topic:
- `/mcp_test_loop/preset` (`std_msgs/String`)

3) **preset-executor ROS2 node** (Python/rclpy) subscribes to that preset topic, and translates
preset -> one or more **traditional ROS2 publishes** (e.g. `/cmd_vel` Twist or `/cmd_vel_stamped` TwistStamped)
using a YAML mapping.

## Repo layout

- `runtime/` – Node.js loop runtime (YAML-configured)
- `python/` – Control-plane MCP server + robot-side executor + Gradio UI
- `vendor/mcp-ros-diagnosis/` – Vendored **observation/diagnosis** MCP servers (SSE) used as an observation source

## Quick start (mock / no robot)

This repo is now self-contained:

- Observation MCP servers are provided via the vendored `vendor/mcp-ros-diagnosis/` project.
- Control MCP server + executor + UI are in `python/`.
- The LoopForever runtime is in `runtime/`.

> Note: `vendor/mcp-ros-diagnosis` requires **Python >=3.10** (same as upstream). On ROS2 Humble (Ubuntu 22.04) you typically have Python 3.10.

### 0) Install Python packages

Create/activate a Python 3.10 environment, then:

```bash
# Observation servers
cd vendor/mcp-ros-diagnosis
pip install -e .

# Control server + executor + UI
cd ../../python
pip install -e .
```

### 1) Start observation MCP servers (mock mode)

```bash
export MANASTONE_MOCK_MODE=true
export MANASTONE_ROBOT_ID=robot_01

# Starts core/joints/power/imu based on vendor config/servers.yaml
manastone-launcher

# core SSE:   http://127.0.0.1:8080/sse
# joints SSE: http://127.0.0.1:8081/sse
```

### 2) Start control plane (safe dry-run)

In a second terminal:

```bash
# Enable control server but keep runtime in dryRun mode initially.
export MANASTONE_ENABLE_CONTROL=true
export MANASTONE_MOTION_CONTROL_TOPIC=/mcp_test_loop/preset

mcp-test-loop-motion-control --port 8087
```

In a third terminal:

```bash
export MANASTONE_PRESET_TOPIC=/mcp_test_loop/preset
export MANASTONE_PRESET_MAPPING=python/config/preset_topic_mapping.yaml

mcp-test-loop-executor
```

In a fourth terminal (optional UI):

```bash
mcp-test-loop-ui
```

### 3) Start loop runtime

```bash
cd runtime
npm install
cp loop.example.yaml loop.yaml

# set your API key env (depends on provider)
# export GOOGLE_API_KEY=...

npm run dev -- --config ./loop.yaml
```

For control during bring-up keep `dryRun: true` in `runtime/loop.yaml`.

## Robot-side setup (recommended)

Use the same components as above, but in **REAL ROS2** mode:

- Run observation servers from `vendor/mcp-ros-diagnosis` with `MANASTONE_MOCK_MODE=false`.
- Run `mcp-test-loop-motion-control` + `mcp-test-loop-executor` on the robot.
- Configure presets + mapping via `mcp-test-loop-ui`.
- Run the loop runtime either on the robot or on a remote machine that can reach the robot MCP SSE endpoints.

The loop writes JSONL logs (configurable) which the UI can display.
