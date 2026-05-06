"""User config loading and access-mode resolution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from planning_mcp.models import AppConfig, AccessMode

CONFIG_PATH = Path.home() / ".planning-mcp" / "config.json"


class ConfigError(ValueError):
    """Raised when the user config exists but cannot be used."""


@dataclass(frozen=True)
class AccessInfo:
    access_mode: AccessMode
    url: str
    server_url: str
    should_open_browser: bool
    ssh_forward: dict[str, object] | None = None


def load_config(path: Path | None = None) -> AppConfig:
    """Load the user config, falling back to local mode when it is absent."""
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        return AppConfig()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Failed to read config file {config_path}: {exc}") from exc
    except JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config file {config_path}: {exc.msg}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {config_path} must contain a JSON object")

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config file {config_path}: {exc}") from exc


def infer_ssh_destination(env: Mapping[str, str] | None = None) -> str | None:
    """Best-effort SSH destination fallback when the config omits it."""
    source_env = os.environ if env is None else env
    ssh_connection = source_env.get("SSH_CONNECTION", "")
    user = source_env.get("USER", "")
    if not ssh_connection or not user:
        return None

    parts = ssh_connection.split()
    if len(parts) < 3:
        return None

    return f"{user}@{parts[2]}"


def resolve_access(
    config: AppConfig,
    server_port: int,
    env: Mapping[str, str] | None = None,
) -> AccessInfo:
    """Resolve user-facing URLs and SSH metadata from config."""
    server_url = f"http://127.0.0.1:{server_port}"

    if config.access_mode == "local":
        return AccessInfo(
            access_mode="local",
            url=server_url,
            server_url=server_url,
            should_open_browser=True,
        )

    if config.access_mode == "external":
        return AccessInfo(
            access_mode="external",
            url=config.public_url,
            server_url=server_url,
            should_open_browser=False,
        )

    local_port = config.local_forward_port or server_port
    destination = config.ssh_destination or infer_ssh_destination(env)
    ssh_forward: dict[str, object] = {
        "local_port": local_port,
        "remote_port": server_port,
        "destination": destination or "",
    }
    if destination:
        ssh_forward["command"] = (
            f"ssh -N -L {local_port}:127.0.0.1:{server_port} {destination}"
        )
    else:
        ssh_forward["hint"] = (
            "Set ssh_destination in ~/.planning-mcp/config.json to generate a complete ssh command."
        )

    return AccessInfo(
        access_mode="ssh",
        url=f"http://127.0.0.1:{local_port}",
        server_url=server_url,
        should_open_browser=False,
        ssh_forward=ssh_forward,
    )
