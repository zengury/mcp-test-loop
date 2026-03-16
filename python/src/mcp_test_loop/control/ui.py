"""Operator UI (Gradio) for mcp-test-loop.

Features (minimal MVP):
- Discover ROS2 topics + types (via ros2 CLI)
- Edit presets allowlist YAML
- Edit preset -> control-topic mapping YAML
- View loop runtime JSONL tail as a lightweight report

Env:
- MANASTONE_MOTION_CONTROL_PRESETS (default: python/config/motion_control_presets.yaml)
- MANASTONE_PRESET_MAPPING (default: python/config/preset_topic_mapping.yaml)
- MANASTONE_LOOP_ACTION_LOG (default: ./mcp_loop_actions.jsonl)
- MANASTONE_MOCK_MODE=true|false
- MANASTONE_UI_HOST (default: 0.0.0.0)
- MANASTONE_UI_PORT (default: 7861)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import gradio as gr
import yaml

from ..discovery import ROS2TopicDiscovery


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


def _discover_topics(filter_substring: str = "", limit: int = 200) -> List[List[str]]:
    mock_mode = os.getenv("MANASTONE_MOCK_MODE", "false").lower() == "true"
    disc = ROS2TopicDiscovery(mock_mode=mock_mode, timeout=3.0)

    import asyncio

    async def _run():
        rows = []
        for ti in await disc.discover(filter_substring=filter_substring.strip(), limit=limit):
            rows.append([ti.topic, ti.message_type])
        return rows

    try:
        return asyncio.run(_run())
    except RuntimeError:
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
    presets_path = Path(os.getenv("MANASTONE_MOTION_CONTROL_PRESETS", "python/config/motion_control_presets.yaml"))
    mapping_path = Path(os.getenv("MANASTONE_PRESET_MAPPING", "python/config/preset_topic_mapping.yaml"))
    log_path = Path(os.getenv("MANASTONE_LOOP_ACTION_LOG", "./mcp_loop_actions.jsonl"))

    with gr.Blocks(title="mcp-test-loop UI") as ui:
        gr.Markdown(
            """
# mcp-test-loop UI

- Topic discovery (topic + type)
- Presets allowlist (for MCP motion-control)
- Preset -> control-topic mapping (for robot-side executor)
- Loop report (JSONL tail)
"""
        )

        with gr.Tab("🔎 Topic Discovery"):
            with gr.Row():
                filter_box = gr.Textbox(label="Filter (substring)", placeholder="e.g. cmd_vel", scale=6)
                limit_box = gr.Number(label="Limit", value=200, precision=0, scale=2)
                discover_btn = gr.Button("Discover", variant="primary", scale=2)
            topics_df = gr.Dataframe(headers=["topic", "type"], interactive=False, wrap=True)
            discover_btn.click(lambda f, l: _discover_topics(f, int(l)), [filter_box, limit_box], [topics_df])

        with gr.Tab("🧩 Presets Allowlist"):
            gr.Markdown(f"**File:** `{presets_path}`")
            presets_editor = gr.Code(
                label="motion_control_presets.yaml",
                language="yaml",
                value=_read_text(presets_path) or "presets:\n  stop:\n    description: 'Stop safely'\n",
            )
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
            mapping_editor = gr.Code(
                label="preset_topic_mapping.yaml",
                language="yaml",
                value=_read_text(mapping_path) or "presets:{}\n",
            )
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
            gr.Markdown("Reads loop runtime JSONL log and shows recent entries.")
            log_path_box = gr.Textbox(label="Action log path", value=str(log_path))
            log_limit_box = gr.Number(label="Tail lines", value=200, precision=0)
            report_btn = gr.Button("Refresh", variant="primary")
            report_json = gr.JSON(label="Recent entries")

            report_btn.click(lambda p, lim: _read_jsonl_tail(Path(p), int(lim)), [log_path_box, log_limit_box], [report_json])

    return ui


def main():
    ui = create_ui()
    host = os.getenv("MANASTONE_UI_HOST", "0.0.0.0")
    port = int(os.getenv("MANASTONE_UI_PORT", "7861"))
    ui.launch(server_name=host, server_port=port)


if __name__ == "__main__":
    main()
