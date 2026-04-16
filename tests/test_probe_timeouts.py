"""Regression tests for probe-timeout protection in storage backend status.

These tests pin the guarantee that the keychain probe cannot stall the MCP
event loop. Without this fence, a locked Secret Service / keychain daemon
could cause the Codex MCP client to close the stdio transport (see the
"Transport closed" issue fixed alongside these tests).
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from labbook import storage
from labbook.auth import _available_storage_backends


class ProbeTimeoutTests(unittest.TestCase):
    def test_keychain_backend_status_times_out_cleanly(self) -> None:
        hang_forever = threading.Event()

        class _SlowKeyring:
            def __init__(self) -> None:
                hang_forever.wait(timeout=5.0)

        def _slow_get_keyring() -> _SlowKeyring:
            return _SlowKeyring()

        try:
            with mock.patch(
                "labbook.storage.keyring.get_keyring", side_effect=_slow_get_keyring
            ):
                started = time.monotonic()
                payload = storage.keychain_backend_status(probe_timeout=0.2)
                elapsed = time.monotonic() - started
        finally:
            hang_forever.set()

        self.assertLess(elapsed, 1.5, "keychain probe must not block past its timeout")
        self.assertFalse(payload["available"])
        self.assertIn("timed out", str(payload["reason"]).lower())
        self.assertTrue(payload["details"].get("probe_timed_out"))

    def test_available_backends_fences_overall_runtime(self) -> None:
        def _slow_keychain(*, probe_timeout: float | None = None) -> dict:
            time.sleep(5.0)
            return {
                "backend": "keychain",
                "display_name": "System Keychain",
                "available": True,
                "selected_by_default": False,
                "reason": None,
                "details": {"keyring_backend": "Keyring", "keyring_module": "x"},
            }

        with mock.patch("labbook.storage._probe_timeout_default", return_value=0.2):
            with mock.patch(
                "labbook.auth.keychain_backend_status",
                side_effect=_slow_keychain,
            ):
                started = time.monotonic()
                result = _available_storage_backends()
                elapsed = time.monotonic() - started

        self.assertLess(
            elapsed,
            2.0,
            "available-backends probe must not hang past its overall budget",
        )
        by_backend = {b["backend"]: b for b in result}
        # When the keychain probe misses the join deadline, the entry is a timeout stub.
        self.assertIn("keychain", by_backend)
        self.assertFalse(by_backend["keychain"]["available"])
        self.assertTrue(by_backend["keychain"]["details"].get("probe_timed_out"))


if __name__ == "__main__":
    unittest.main()
