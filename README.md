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
- `python/` – MCP servers + ROS2 executor + Gradio UI

## Quick start (mock / no robot)

You can run the loop runtime without ROS2 by pointing it at real MCP observation servers.
For control, keep `dryRun: true`.

## Robot-side setup (recommended)

### 1) Start motion-control MCP server (on robot)

```bash
export MANASTONE_ENABLE_CONTROL=true
export MANASTONE_MOTION_CONTROL_TOPIC=/mcp_test_loop/preset
python -m mcp_test_loop.servers.motion_control --port 8087
```

### 2) Start preset executor (on robot)

```bash
export MANASTONE_PRESET_TOPIC=/mcp_test_loop/preset
export MANASTONE_PRESET_MAPPING=config/preset_topic_mapping.yaml
python -m mcp_test_loop.control.preset_executor
```

### 3) Configure presets + mappings via UI

```bash
python -m mcp_test_loop.control.ui --port 7861
```

## Loop runtime

```bash
cd runtime
npm install
cp loop.example.yaml loop.yaml
# set your API key env (depends on provider)
# export GOOGLE_API_KEY=...

npm run dev -- --config ./loop.yaml
```

The loop writes JSONL logs (configurable) which the UI can display.
