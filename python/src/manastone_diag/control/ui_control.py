"""Manastone Control UI (Gradio)

This UI helps operators configure motion presets at runtime:
- Discover available ROS2 topics (topic name + message type)
- Edit preset allowlist for the MCP control server
- Edit preset -> control-topic publish mapping used by the robot-side executor
- View a lightweight test report from the loop runtime JSONL log

This module intentionally keeps logic simple and file-based (YAML + JSONL),
so it can run offline on the robot and be audited.

Env:
- MANASTONE_MOTION_CONTROL_PRESETS (default: config/motion_control_presets.yaml)
- MANASTONE_PRESET_MAPPING (default: config/preset_topic_mapping.yaml)
- MANASTONE_LOOP_ACTION_LOG (optional default: ./mcp_loop_actions.jsonl)

Run:
  manastone-control-ui
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import gradio as gr
import yaml

from ..discovery.ros2_discovery import ROS2Discovery


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _validate_yaml(text: str) -> tuple[bool, str]:
    try:
        obj = yaml.safe_load(text) or {}
        if not isinstance(obj, dict):
            return False, "YAML root must be a mapping (dict)."
        return True, "OK"
    except Exception as e:
        return False, str(e)


def _discover_topics(filter_regex: str = "", limit: int = 200) -> List[List[str]]:
    """Return rows: [topic, type]."""
    # We rely on ros2 CLI; when MANASTONE_MOCK_MODE=true, use mock discovery.
    mock_mode = os.getenv("MANASTONE_MOCK_MODE", "false").lower() == "true"
    disc = ROS2Discovery(mock_mode=mock_mode, timeout=3.0)

    # ROS2Discovery.discover_all() is async; in UI we do a minimal sync wrapper.
    import asyncio

    async def _run():
        topics = await disc._list_topics() if not mock_mode else [t.topic for t in disc._mock_discovery()]
        rows = []
        for t in topics:
            if filter_regex and filter_regex not in t:
                # simple substring filter to avoid regex engine surprises in UI
                continue
            ty = await disc._get_topic_type(t) if not mock_mode else next((x.message_type for x in disc._mock_discovery() if x.topic == t), "unknown")
            rows.append([t, ty])
            if len(rows) >= limit:
                break
        return rows

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # If already in an event loop, fallback to new loop
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()


def _read_jsonl_tail(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"raw": line})
    return out


def create_ui() -> gr.Blocks:
    presets_path = Path(os.getenv("MANASTONE_MOTION_CONTROL_PRESETS", "config/motion_control_presets.yaml"))
    mapping_path = Path(os.getenv("MANASTONE_PRESET_MAPPING", "config/preset_topic_mapping.yaml"))
    log_path = Path(os.getenv("MANASTONE_LOOP_ACTION_LOG", "./mcp_loop_actions.jsonl"))

    with gr.Blocks(title="Manastone Control UI") as ui:
        gr.Markdown(
            """
# Manastone Control UI

用于现场配置 **motion presets** 与 **preset→控制topic映射**，并查看持久性测试的简易报告。

- 发现话题：使用 ros2 CLI 遍历 topics + type（与 discovery 模块同源）
- presets allowlist：供 MCP server `manastone-motion-control` 执行
- mapping：供 robot-side `manastone-motion-executor` 把 preset 翻译成传统 ROS2 publish
"""
        )

        with gr.Tab("🔎 Topic Discovery"):
            with gr.Row():
                filter_box = gr.Textbox(label="Filter (substring)", placeholder="e.g. cmd_vel", scale=6)
                limit_box = gr.Number(label="Limit", value=200, precision=0, scale=2)
                discover_btn = gr.Button("Discover", variant="primary", scale=2)
            topics_df = gr.Dataframe(headers=["topic", "type"], interactive=False, wrap=True)

            def on_discover(filt: str, limit: float):
                rows = _discover_topics(filt.strip(), int(limit))
                return rows

            discover_btn.click(on_discover, [filter_box, limit_box], [topics_df])

        with gr.Tab("🧩 Presets Allowlist (MCP)"):
            gr.Markdown(f"**File:** `{presets_path}`")
            presets_editor = gr.Code(label="motion_control_presets.yaml", language="yaml", value=_read_text(presets_path) or "presets:\n  stop:\n    description: 'Stop safely'\n")
            with gr.Row():
                presets_load = gr.Button("Reload")
                presets_validate = gr.Button("Validate")
                presets_save = gr.Button("Save", variant="primary")
            presets_status = gr.Markdown()

            presets_load.click(lambda: _read_text(presets_path), outputs=[presets_editor])
            presets_validate.click(lambda txt: _validate_yaml(txt)[1], inputs=[presets_editor], outputs=[presets_status])

            def on_save_presets(txt: str):
                ok, msg = _validate_yaml(txt)
                if not ok:
                    return f"❌ YAML invalid: {msg}"
                _write_text(presets_path, txt)
                return "✅ Saved"

            presets_save.click(on_save_presets, [presets_editor], [presets_status])

        with gr.Tab("🗺️ Preset → Control Topic Mapping"):
            gr.Markdown(f"**File:** `{mapping_path}`")
            mapping_editor = gr.Code(label="preset_topic_mapping.yaml", language="yaml", value=_read_text(mapping_path) or "presets:{}\n")
            with gr.Row():
                mapping_load = gr.Button("Reload")
                mapping_validate = gr.Button("Validate")
                mapping_save = gr.Button("Save", variant="primary")
            mapping_status = gr.Markdown()

            mapping_load.click(lambda: _read_text(mapping_path), outputs=[mapping_editor])
            mapping_validate.click(lambda txt: _validate_yaml(txt)[1], inputs=[mapping_editor], outputs=[mapping_status])

            def on_save_mapping(txt: str):
                ok, msg = _validate_yaml(txt)
                if not ok:
                    return f"❌ YAML invalid: {msg}"
                _write_text(mapping_path, txt)
                return "✅ Saved"

            mapping_save.click(on_save_mapping, [mapping_editor], [mapping_status])

        with gr.Tab("📈 Loop Report"):
            gr.Markdown("读取 loop runtime 的 JSONL 日志，展示最近决策/动作。")
            log_path_box = gr.Textbox(label="Action log path", value=str(log_path))
            log_limit_box = gr.Number(label="Tail lines", value=200, precision=0)
            report_btn = gr.Button("Refresh", variant="primary")
            report_json = gr.JSON(label="Recent entries")

            def on_report(p: str, lim: float):
                return _read_jsonl_tail(Path(p), int(lim))

            report_btn.click(on_report, [log_path_box, log_limit_box], [report_json])

    return ui


def main():
    ui = create_ui()
    host = os.getenv("MANASTONE_UI_HOST", "0.0.0.0")
    port = int(os.getenv("MANASTONE_UI_PORT", "7861"))
    ui.launch(server_name=host, server_port=port)


if __name__ == "__main__":
    main()
