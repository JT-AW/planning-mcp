from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from planning_mcp.config import ConfigError, infer_ssh_destination, load_config, resolve_access
from planning_mcp.models import AppConfig


class ConfigTests(unittest.TestCase):
    def test_missing_config_defaults_to_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(Path(tmpdir) / "config.json")

        self.assertEqual(config.access_mode, "local")
        self.assertEqual(config.local_forward_port, 0)

    def test_invalid_json_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text("{not-json}", encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_config(path)

    def test_external_mode_requires_public_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(json.dumps({"access_mode": "external"}), encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_config(path)

    def test_infer_ssh_destination_from_environment(self) -> None:
        destination = infer_ssh_destination(
            {
                "USER": "jtaw",
                "SSH_CONNECTION": "100.0.0.1 12345 100.0.0.2 22",
            }
        )

        self.assertEqual(destination, "jtaw@100.0.0.2")

    def test_resolve_ssh_access_uses_forward_port_and_command(self) -> None:
        access = resolve_access(
            AppConfig(access_mode="ssh", ssh_destination="jtaw@spark-fb5b", local_forward_port=7777),
            59153,
        )

        self.assertEqual(access.url, "http://127.0.0.1:7777")
        self.assertEqual(access.server_url, "http://127.0.0.1:59153")
        self.assertIsNotNone(access.ssh_forward)
        assert access.ssh_forward is not None
        self.assertEqual(
            access.ssh_forward["command"],
            "ssh -N -L 7777:127.0.0.1:59153 jtaw@spark-fb5b",
        )

    def test_resolve_ssh_access_without_destination_returns_hint(self) -> None:
        access = resolve_access(AppConfig(access_mode="ssh"), 59153, env={})

        self.assertIsNotNone(access.ssh_forward)
        assert access.ssh_forward is not None
        self.assertNotIn("command", access.ssh_forward)
        self.assertIn("hint", access.ssh_forward)


if __name__ == "__main__":
    unittest.main()
