"""manastone motion preset executor (robot-side).

This ROS2 node subscribes to a preset topic (std_msgs/String) and translates preset
names into *traditional ROS2 control* publishes (topic + message).

It is meant to be used together with the MCP server `manastone-motion-control`:

- MCP server tool `execute_preset(preset)` publishes preset name to
  `/mcp_test_loop/preset` (std_msgs/String)
- This executor subscribes to that topic, looks up the preset in a YAML mapping,
  and publishes a configured message to your robot's control topic.

Why this split?
- MCP server stays safe: allowlist + simple publish of intent.
- Robot-side mapping is explicit, reviewable, and can be adjusted without LLM.

Env vars:
- MANASTONE_PRESET_TOPIC (default: /mcp_test_loop/preset)
- MANASTONE_PRESET_MAPPING (default: config/preset_topic_mapping.yaml)

YAML format (example):

presets:
  stop:
    publish:
      - topic: /cmd_vel
        type: geometry_msgs/msg/Twist
        msg:
          linear: {x: 0.0, y: 0.0, z: 0.0}
          angular: {x: 0.0, y: 0.0, z: 0.0}

  heart_hands:
    publish:
      - topic: /gesture
        type: std_msgs/msg/String
        msg:
          data: heart_hands

Notes:
- `type` must be a ROS2 interface type string: <pkg>/msg/<MsgName>
- `msg` is a dict mapped into the ROS message fields recursively.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any, Dict

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

import yaml

logger = logging.getLogger("manastone.preset_executor")


def _import_msg_class(type_str: str):
    """Import ROS2 msg class from <pkg>/msg/<Name>."""
    parts = type_str.split("/")
    if len(parts) != 3 or parts[1] != "msg":
        raise ValueError(f"Invalid msg type '{type_str}'. Expected '<pkg>/msg/<Name>'")
    pkg, _, name = parts
    module = importlib.import_module(f"{pkg}.msg")
    cls = getattr(module, name, None)
    if cls is None:
        raise ValueError(f"Message class not found: {type_str}")
    return cls


def _is_now_macro(v: Any) -> bool:
    return isinstance(v, dict) and v.get("__now__") is True


def _apply_dict_to_msg(msg_obj: Any, data: Dict[str, Any], now_time_msg: Any) -> None:
    """Recursively apply dict values to ROS2 message instance fields.

    Supports macro: {__now__: true} for Time-like fields (sec/nanosec).
    """
    for k, v in data.items():
        if not hasattr(msg_obj, k):
            raise ValueError(f"Message {type(msg_obj).__name__} has no field '{k}'")

        cur = getattr(msg_obj, k)

        if _is_now_macro(v):
            # Only intended for builtin_interfaces/msg/Time-like objects
            if hasattr(cur, "sec") and hasattr(cur, "nanosec"):
                cur.sec = now_time_msg.sec
                cur.nanosec = now_time_msg.nanosec
                continue
            raise ValueError(f"__now__ macro used on non-Time field: {type(cur).__name__}")

        if isinstance(v, dict):
            _apply_dict_to_msg(cur, v, now_time_msg)
        else:
            setattr(msg_obj, k, v)


def _load_mapping(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Preset mapping not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("Preset mapping YAML must be a dict")
    if "presets" not in data or not isinstance(data.get("presets"), dict):
        raise ValueError("Preset mapping YAML must contain 'presets' mapping")
    return data


class PresetExecutor(Node):
    def __init__(self):
        super().__init__("manastone_preset_executor")

        self.preset_topic = os.getenv("MANASTONE_PRESET_TOPIC", "/mcp_test_loop/preset")
        mapping_path = Path(os.getenv("MANASTONE_PRESET_MAPPING", "config/preset_topic_mapping.yaml"))
        self.mapping_path = mapping_path
        self.mapping = _load_mapping(mapping_path)

        self._publishers = {}  # (topic, type_str) -> publisher

        self.sub = self.create_subscription(String, self.preset_topic, self._on_preset, 10)

        self.get_logger().info(f"PresetExecutor ready. preset_topic={self.preset_topic}")
        self.get_logger().info(f"Mapping: {self.mapping_path}")

    def _get_publisher(self, topic: str, type_str: str):
        key = (topic, type_str)
        pub = self._publishers.get(key)
        if pub is not None:
            return pub
        cls = _import_msg_class(type_str)
        pub = self.create_publisher(cls, topic, 10)
        self._publishers[key] = pub
        return pub

    def _on_preset(self, msg: String):
        preset = (msg.data or "").strip()
        if not preset:
            return

        presets: dict = self.mapping.get("presets") or {}
        spec = presets.get(preset)
        if spec is None:
            self.get_logger().warn(f"Unknown preset '{preset}' (not in mapping). Ignored.")
            return

        actions = spec.get("publish")
        if not isinstance(actions, list) or len(actions) == 0:
            self.get_logger().warn(f"Preset '{preset}' has no 'publish' actions. Ignored.")
            return

        now_time_msg = self.get_clock().now().to_msg()

        for a in actions:
            try:
                topic = a.get("topic")
                type_str = a.get("type")
                msg_dict = a.get("msg")
                if not isinstance(topic, str) or not topic:
                    raise ValueError("publish.topic must be a non-empty string")
                if not isinstance(type_str, str) or not type_str:
                    raise ValueError("publish.type must be a non-empty string")
                if msg_dict is None:
                    msg_dict = {}
                if not isinstance(msg_dict, dict):
                    raise ValueError("publish.msg must be a dict")

                pub = self._get_publisher(topic, type_str)
                cls = _import_msg_class(type_str)
                out = cls()
                _apply_dict_to_msg(out, msg_dict, now_time_msg)
                pub.publish(out)
                self.get_logger().info(f"preset='{preset}' -> published {type_str} to {topic}")
            except Exception as e:
                self.get_logger().error(f"Failed to execute preset '{preset}': {e}")


def main():
    logging.basicConfig(level=logging.INFO)
    rclpy.init(args=None)
    try:
        node = PresetExecutor()
        rclpy.spin(node)
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()
