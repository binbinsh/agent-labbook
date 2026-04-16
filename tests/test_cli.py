from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from labbook import cli
from labbook.cli import main


class CliTests(unittest.TestCase):
    def test_print_mcp_config_defaults_to_pypi_package(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["print-mcp-config", "--server-name", "labbook"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload,
            {
                "mcpServers": {
                    "labbook": {
                        "command": "uvx",
                        "args": ["agent-labbook", "mcp"],
                    }
                }
            },
        )

    def test_doctor_surfaces_secret_plan(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "labbook.cli.status",
                return_value={
                    "secret_plan": {
                        "mode": "keychain",
                        "reason": "System keychain is the default recommendation for persistent local development on this machine.",
                    },
                },
            ):
                with redirect_stdout(stdout):
                    exit_code = main(["doctor", "--project-root", tmpdir])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload["secret_plan"],
            {
                "mode": "keychain",
                "reason": "System keychain is the default recommendation for persistent local development on this machine.",
            },
        )

    def test_configure_secret_prompts_locally_and_stores_secret(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "labbook.cli._prompt_for_secret_twice",
                return_value="secret_test_token",
            ) as prompt_mock:
                with mock.patch(
                    "labbook.cli.configure_internal_integration",
                    return_value={"ok": True, "storage": "keychain"},
                ) as configure_mock:
                    with redirect_stdout(stdout):
                        exit_code = main(
                            [
                                "configure-secret",
                                "--project-root",
                                tmpdir,
                                "--storage",
                                "keychain",
                            ]
                        )

        self.assertEqual(exit_code, 0)
        prompt_mock.assert_called_once_with()
        configure_mock.assert_called_once()
        self.assertEqual(
            configure_mock.call_args.kwargs,
            {
                "secret": "secret_test_token",
                "project_root": mock.ANY,
                "storage": "keychain",
            },
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, {"ok": True, "storage": "keychain"})

    def test_prompt_for_secret_twice_rejects_mismatch(self) -> None:
        with mock.patch(
            "labbook.cli.getpass.getpass",
            side_effect=["secret_one", "secret_two"],
        ):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                cli._prompt_for_secret_twice()

    def test_prompt_for_secret_once_rejects_insecure_fallback(self) -> None:
        with mock.patch(
            "labbook.cli.getpass.getpass",
            side_effect=cli.getpass.GetPassWarning("tty missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, "securely"):
                cli._prompt_for_secret_once("Secret: ")


if __name__ == "__main__":
    unittest.main()
