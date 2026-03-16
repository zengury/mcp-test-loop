"""ROS2 topic discovery (minimal).

This mirrors the approach used in manastone's ROS2Discovery:
- `ros2 topic list`
- `ros2 topic info <topic>` to get message type

We keep it minimal because for preset mapping we mainly need (topic, type).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TopicInfo:
    topic: str
    message_type: str


class ROS2TopicDiscovery:
    def __init__(self, timeout: float = 3.0, mock_mode: bool = False):
        self.timeout = timeout
        self.mock_mode = mock_mode

    async def _run(self, cmd: List[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            return stdout.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            proc.kill()
            return ""

    async def list_topics(self) -> List[str]:
        if self.mock_mode:
            return ["/cmd_vel", "/cmd_vel_stamped", "/diagnostics"]
        out = await self._run(["ros2", "topic", "list"])
        return [t.strip() for t in out.splitlines() if t.strip()]

    async def get_type(self, topic: str) -> str:
        if self.mock_mode:
            if topic == "/cmd_vel":
                return "geometry_msgs/msg/Twist"
            if topic == "/cmd_vel_stamped":
                return "geometry_msgs/msg/TwistStamped"
            return "unknown"

        out = await self._run(["ros2", "topic", "info", topic])
        for line in out.splitlines():
            if "Type:" in line:
                return line.split(":", 1)[1].strip()
        return "unknown"

    async def discover(self, filter_substring: str = "", limit: int = 200) -> List[TopicInfo]:
        topics = await self.list_topics()
        out: List[TopicInfo] = []
        for t in topics:
            if filter_substring and filter_substring not in t:
                continue
            ty = await self.get_type(t)
            out.append(TopicInfo(topic=t, message_type=ty))
            if len(out) >= limit:
                break
        return out
