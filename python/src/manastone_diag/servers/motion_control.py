"""
manastone-motion-control
运动控制（写入）MCP Server —— 用于执行预设动作 / 下发控制指令。

设计目标：
- 与诊断类 server 分离（避免只读/可控混用）
- 控制面最小化：仅允许执行 YAML allowlist 里的 preset
- 执行方式使用 ROS2 传统方式：在 server 内启动一个 rclpy Node，然后向 topic 发布 preset 名称
- 真正的动作执行由机器人侧既有组件完成（订阅该 topic 并执行 preset）

工具：
  list_presets              - 列出可执行 presets
  execute_preset(preset)    - 发布 preset 名称（std_msgs/String）

环境变量：
  MANASTONE_ENABLE_CONTROL=true       # 必须显式开启控制（默认禁用）
  MANASTONE_MOTION_CONTROL_PRESETS=...  # YAML 路径（默认：config/motion_control_presets.yaml）
  MANASTONE_MOTION_CONTROL_TOPIC=...    # 发布 topic（默认：/mcp_test_loop/preset）

YAML 格式：
  presets:
    heart_hands:
      description: "Make a heart gesture"
    stop:
      description: "Stop safely"
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

from .base import AppState, init_shared_state, shutdown_shared_state, get_shared_state

logger = logging.getLogger(__name__)

# ── ROS2 publisher state (lazy) ───────────────────────────────────────────────
_ros_lock = threading.Lock()
_ros_ready = False
_ros_node: Optional[object] = None
_ros_publisher: Optional[object] = None
_ros_executor: Optional[object] = None
_ros_thread: Optional[threading.Thread] = None


def _load_presets() -> dict:
    preset_path = Path(
        os.getenv("MANASTONE_MOTION_CONTROL_PRESETS", "config/motion_control_presets.yaml")
    )
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
    """Create a minimal ROS2 node + std_msgs/String publisher and spin it in a thread."""
    global _ros_ready, _ros_node, _ros_publisher, _ros_executor, _ros_thread

    with _ros_lock:
        if _ros_ready:
            return

        # Lazy import: allows mock mode usage without ROS2 installed.
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_msgs.msg import String

        if not rclpy.ok():
            rclpy.init(args=None)

        topic = os.getenv("MANASTONE_MOTION_CONTROL_TOPIC", "/mcp_test_loop/preset")

        node = Node("manastone_motion_control_mcp")
        pub = node.create_publisher(String, topic, 10)

        executor = SingleThreadedExecutor()
        executor.add_node(node)

        t = threading.Thread(
            target=executor.spin,
            name="manastone_motion_control_mcp_spin",
            daemon=True,
        )
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
async def _lifespan(server: FastMCP, **kwargs) -> AsyncIterator[AppState]:
    state = await init_shared_state(**kwargs)
    logger.info("manastone-motion-control ready")
    try:
        yield state
    finally:
        _shutdown_ros()
        await shutdown_shared_state()


def create_server(**init_kwargs) -> FastMCP:
    from functools import partial

    mcp = FastMCP(
        "manastone-motion-control",
        lifespan=partial(_lifespan, **init_kwargs),
    )

    @mcp.tool()
    async def list_presets(ctx: Context = None) -> str:
        """List available motion presets (allowlist)."""
        _ = get_shared_state()  # ensure state initialized
        data = _load_presets()
        presets = data.get("presets") or {}
        names = sorted(list(presets.keys()))
        return json.dumps(
            {
                "status": "ok",
                "count": len(names),
                "presets": [
                    {
                        "name": n,
                        "description": (presets.get(n) or {}).get("description", ""),
                    }
                    for n in names
                ],
                "topic": os.getenv(
                    "MANASTONE_MOTION_CONTROL_TOPIC", "/mcp_test_loop/preset"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    @mcp.tool()
    async def execute_preset(
        preset: str,
        dry_run: bool = False,
        ctx: Context = None,
    ) -> str:
        """Execute a motion preset by publishing the preset name to a ROS2 topic."""
        s = get_shared_state()

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

        if s.mock_mode or dry_run:
            return json.dumps(
                {
                    "status": "ok",
                    "executed": False,
                    "mock_mode": s.mock_mode,
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
    """独立运行单个 server（不通过 launcher）"""
    init_kwargs = {
        "schema_path": Path(os.getenv("MANASTONE_SCHEMA_PATH", "config/robot_schema.yaml")),
        "storage_dir": Path(os.getenv("MANASTONE_STORAGE_DIR", "storage")),
        "robot_id": os.getenv("MANASTONE_ROBOT_ID", "robot_01"),
        "mock_mode": os.getenv("MANASTONE_MOCK_MODE", "false").lower() == "true",
    }
    mcp = create_server(**init_kwargs)
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.getenv("MANASTONE_PORT", "8087"))
    mcp.run(transport=os.getenv("MANASTONE_TRANSPORT", "sse"))
