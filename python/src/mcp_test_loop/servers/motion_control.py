"""mcp-test-loop motion-control MCP server.

This server is **control-plane** (write). It publishes a preset name to a ROS2 topic.

Tools:
- list_presets(): list allowlisted presets
- execute_preset(preset, dry_run=False): publish preset name (std_msgs/String)

Env:
- MANASTONE_ENABLE_CONTROL=true (required for real execution)
- MANASTONE_MOTION_CONTROL_PRESETS (default: python/config/motion_control_presets.yaml)
- MANASTONE_MOTION_CONTROL_TOPIC (default: /mcp_test_loop/preset)
- MANASTONE_MOCK_MODE=true|false

YAML format:

presets:
  stop:
    description: "Stop safely"
  heart_hands:
    description: "Make a heart gesture"
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

import yaml
from mcp.server.fastmcp import FastMCP, Context

logger = logging.getLogger(__name__)

# ROS2 publisher state (lazy)
_ros_lock = threading.Lock()
_ros_ready = False
_ros_node: Optional[object] = None
_ros_publisher: Optional[object] = None
_ros_executor: Optional[object] = None
_ros_thread: Optional[threading.Thread] = None


def _load_presets() -> dict:
    preset_path = Path(os.getenv("MANASTONE_MOTION_CONTROL_PRESETS", "python/config/motion_control_presets.yaml"))
    if not preset_path.exists():
        return {"presets": {}}

    with open(preset_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        return {"presets": {}}

    presets = data.get("presets")
    if not isinstance(presets, dict):
        return {"presets": {}}

    return data


def _ensure_ros_publisher() -> None:
    global _ros_ready, _ros_node, _ros_publisher, _ros_executor, _ros_thread

    with _ros_lock:
        if _ros_ready:
            return

        # Lazy import so the server can start in dry_run/mock mode without ROS2.
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_msgs.msg import String

        if not rclpy.ok():
            rclpy.init(args=None)

        topic = os.getenv("MANASTONE_MOTION_CONTROL_TOPIC", "/mcp_test_loop/preset")

        node = Node("mcp_test_loop_motion_control")
        pub = node.create_publisher(String, topic, 10)

        executor = SingleThreadedExecutor()
        executor.add_node(node)

        t = threading.Thread(target=executor.spin, name="mcp_test_loop_motion_control_spin", daemon=True)
        t.start()

        _ros_node = node
        _ros_publisher = pub
        _ros_executor = executor
        _ros_thread = t
        _ros_ready = True


def _shutdown_ros() -> None:
    global _ros_ready, _ros_node, _ros_publisher, _ros_executor

    with _ros_lock:
        if not _ros_ready:
            return

        try:
            import rclpy

            if _ros_executor and _ros_node:
                try:
                    _ros_executor.remove_node(_ros_node)
                except Exception:
                    pass

            if _ros_node:
                try:
                    _ros_node.destroy_node()
                except Exception:
                    pass

            if rclpy.ok():
                try:
                    rclpy.shutdown()
                except Exception:
                    pass

        finally:
            _ros_ready = False
            _ros_node = None
            _ros_publisher = None
            _ros_executor = None


@asynccontextmanager
async def _lifespan(_server: FastMCP, **_kwargs) -> AsyncIterator[None]:
    logger.info("mcp-test-loop motion-control ready")
    try:
        yield None
    finally:
        _shutdown_ros()


def create_server() -> FastMCP:
    mcp = FastMCP("mcp-test-loop-motion-control", lifespan=_lifespan)

    @mcp.tool()
    async def list_presets(ctx: Context = None) -> str:
        data = _load_presets()
        presets = data.get("presets") or {}
        names = sorted(list(presets.keys()))
        return json.dumps(
            {
                "status": "ok",
                "count": len(names),
                "presets": [
                    {"name": n, "description": (presets.get(n) or {}).get("description", "")} for n in names
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    @mcp.tool()
    async def execute_preset(preset: str, dry_run: bool = False, ctx: Context = None) -> str:
        mock_mode = os.getenv("MANASTONE_MOCK_MODE", "false").lower() == "true"

        if os.getenv("MANASTONE_ENABLE_CONTROL", "false").lower() != "true":
            return json.dumps(
                {
                    "status": "error",
                    "message": "Control disabled. Set MANASTONE_ENABLE_CONTROL=true to enable.",
                    "preset": preset,
                },
                ensure_ascii=False,
                indent=2,
            )

        data = _load_presets()
        presets = data.get("presets") or {}
        if preset not in presets:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Unknown preset: {preset}",
                    "available": sorted(list(presets.keys())),
                },
                ensure_ascii=False,
                indent=2,
            )

        topic = os.getenv("MANASTONE_MOTION_CONTROL_TOPIC", "/mcp_test_loop/preset")

        if mock_mode or dry_run:
            return json.dumps(
                {
                    "status": "ok",
                    "executed": False,
                    "mock_mode": mock_mode,
                    "dry_run": dry_run,
                    "preset": preset,
                    "transport": "ros2_topic",
                    "topic": topic,
                },
                ensure_ascii=False,
                indent=2,
            )

        try:
            _ensure_ros_publisher()
            from std_msgs.msg import String

            msg = String()
            msg.data = preset
            _ros_publisher.publish(msg)  # type: ignore

            return json.dumps(
                {
                    "status": "ok",
                    "executed": True,
                    "preset": preset,
                    "transport": "ros2_topic",
                    "topic": topic,
                },
                ensure_ascii=False,
                indent=2,
            )

        except Exception as e:
            return json.dumps(
                {
                    "status": "error",
                    "message": str(e),
                    "preset": preset,
                    "transport": "ros2_topic",
                    "topic": topic,
                },
                ensure_ascii=False,
                indent=2,
            )

    return mcp


def main():
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("MANASTONE_PORT", "8087")))
    args = parser.parse_args()

    mcp = create_server()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
