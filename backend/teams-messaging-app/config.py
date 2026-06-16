"""Application configuration loaded from environment variables.

A single immutable :class:`AppConfig` instance is loaded at startup and
passed to the composition root. Modules should accept the values they need,
not the whole config, to keep dependencies explicit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for the Teams bot."""

    project_endpoint: str
    agent_name: str
    port: int
    broadcast_command: str

    @classmethod
    def from_env(cls) -> AppConfig:
        load_dotenv()
        project_endpoint = (os.getenv("PROJECT_ENDPOINT") or "").strip()
        agent_name = (os.getenv("AGENT_NAME") or "").strip()
        if not project_endpoint or not agent_name:
            raise RuntimeError(
                "PROJECT_ENDPOINT and AGENT_NAME must be set in the "
                "environment (see .env)."
            )
        return cls(
            project_endpoint=project_endpoint,
            agent_name=agent_name,
            port=int(os.getenv("PORT", "3978")),
            broadcast_command=(
                os.getenv("BROADCAST_COMMAND", "broadcast").strip().lower()
            ),
        )
