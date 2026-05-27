"""Foundry prompt-agent YAML configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_YAML_PATH = Path(__file__).parent / "case_assistant_agent.yaml"
_WORKSPACE_ROOT = Path(__file__).parent.parent.parent.parent

def load_agent_yaml(yaml_path: Path | str | None = None) -> dict[str, Any]:
    """Load and normalize prompt-agent YAML content."""
    if yaml_path is None:
        path = _YAML_PATH
    else:
        path = Path(yaml_path)
        if not path.is_absolute():
            path = _WORKSPACE_ROOT / yaml_path

    if not path.exists():
        raise FileNotFoundError(f"Agent YAML file not found: {path}")

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError("Agent YAML file is empty")

    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Agent YAML must be a mapping at the top level")

    definition: dict[str, Any] = parsed.get("definition") or {}
    model = os.getenv("FOUNDRY_MODEL", "").strip()
    if not model:
        raise ValueError("FOUNDRY_MODEL environment variable is required but not set")

    return {
        "name": str(parsed.get("name") or ""),
        "version": str(parsed.get("version") or "1"),
        "description": str(parsed.get("description") or ""),
        "model": model,
        "instructions": str(definition.get("instructions") or ""),
        "temperature": float(definition.get("temperature", 1.0)),
        "top_p": float(definition.get("top_p", 1.0)),
        "tools": definition.get("tools") or [],
    }


class CaseAssistantAgentConfig:
    """Return a Foundry-compatible prompt-agent configuration dictionary."""

    @staticmethod
    def get_agent_config(yaml_path: Path | str | None = None) -> dict[str, Any]:
        cfg = load_agent_yaml(yaml_path)
        return {
            "model": cfg["model"],
            "name": cfg["name"],
            "description": cfg["description"],
            "instructions": cfg["instructions"],
            "tools": cfg["tools"],
            "temperature": cfg["temperature"],
            "top_p": cfg["top_p"],
        }
