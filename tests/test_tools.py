from __future__ import annotations

import unittest
from unittest.mock import patch

from planning_mcp.models import AppConfig
from planning_mcp.tools import open_plan


class OpenPlanTests(unittest.TestCase):
    @patch("planning_mcp.tools.open_browser_async")
    @patch("planning_mcp.tools.start_web_server", return_value=59153)
    @patch("planning_mcp.tools.load_config", return_value=AppConfig())
    def test_open_plan_local_mode_opens_browser(
        self,
        _load_config: object,
        _start_web_server: object,
        open_browser_async: object,
    ) -> None:
        result = open_plan(plan_markdown="# Plan", plan_title="Test Plan")

        self.assertEqual(result["access_mode"], "local")
        self.assertEqual(result["url"], "http://127.0.0.1:59153")
        self.assertEqual(result["server_url"], "http://127.0.0.1:59153")
        self.assertEqual(result["opened_browser"], True)
        open_browser_async.assert_called_once_with("http://127.0.0.1:59153")

    @patch("planning_mcp.tools.open_browser_async")
    @patch("planning_mcp.tools.start_web_server", return_value=59153)
    @patch(
        "planning_mcp.tools.load_config",
        return_value=AppConfig(
            access_mode="ssh",
            ssh_destination="jtaw@spark-fb5b",
            local_forward_port=7777,
        ),
    )
    def test_open_plan_ssh_mode_returns_forwarding_metadata(
        self,
        _load_config: object,
        _start_web_server: object,
        open_browser_async: object,
    ) -> None:
        result = open_plan(plan_markdown="# Plan", plan_title="Test Plan")

        self.assertEqual(result["access_mode"], "ssh")
        self.assertEqual(result["url"], "http://127.0.0.1:7777")
        self.assertEqual(result["server_url"], "http://127.0.0.1:59153")
        self.assertEqual(result["opened_browser"], False)
        self.assertEqual(
            result["ssh_forward"],
            {
                "local_port": 7777,
                "remote_port": 59153,
                "destination": "jtaw@spark-fb5b",
                "command": "ssh -N -L 7777:127.0.0.1:59153 jtaw@spark-fb5b",
            },
        )
        open_browser_async.assert_not_called()

    @patch("planning_mcp.tools.open_browser_async")
    @patch("planning_mcp.tools.start_web_server", return_value=59153)
    @patch(
        "planning_mcp.tools.load_config",
        return_value=AppConfig(access_mode="external", public_url="https://plans.example.com"),
    )
    def test_open_plan_external_mode_returns_public_url(
        self,
        _load_config: object,
        _start_web_server: object,
        open_browser_async: object,
    ) -> None:
        result = open_plan(plan_markdown="# Plan", plan_title="Test Plan")

        self.assertEqual(result["access_mode"], "external")
        self.assertEqual(result["url"], "https://plans.example.com")
        self.assertEqual(result["server_url"], "http://127.0.0.1:59153")
        self.assertEqual(result["opened_browser"], False)
        self.assertNotIn("ssh_forward", result)
        open_browser_async.assert_not_called()


if __name__ == "__main__":
    unittest.main()
