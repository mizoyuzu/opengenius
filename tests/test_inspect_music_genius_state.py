"""Stored-state preflight must not confuse a partial enable with readiness."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inspect_music_genius_state import inspect_bundle_state
from prepare_music_genius_state import encode_preferences


def state_fixture(state, suffix=b"k" * 16):
    # Only the observed plma/key-header fragment. Not a native Music library.
    expanded = bytearray(194)
    expanded[:4] = b"plma"
    struct.pack_into("<I", expanded, 4, 194)
    expanded[12] = state
    key_header = b"\x00\x01\x00\x00" + suffix
    expanded.extend(b"boma" + struct.pack("<IIII", 20, 40, 502, 0) + key_header)
    header = bytearray(160)
    header[:4] = b"hfma"
    struct.pack_into("<II", header, 4, 160, 160)
    struct.pack_into("<I", header, 84, 102400)
    return encode_preferences(bytes(expanded), bytes(header))


class StoredStatePreflightTests(unittest.TestCase):
    def bundle(self, root, states=(0, 1), key_suffix=b"k" * 16):
        (root / "Library.musicdb").write_bytes(state_fixture(states[0]))
        (root / "Library Preferences.musicdb").write_bytes(state_fixture(states[1], key_suffix))

    def test_partial_enable_is_rejected_by_cli_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.bundle(root)
            original = {p.name: p.read_bytes() for p in root.iterdir()}
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" /
                       "inspect_music_genius_state.py"), str(root), "--require-enabled"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "state_mismatch")
            self.assertFalse(report["stored_locally_enabled"])
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, original)
            self.bundle(root, (1, 1))
            accepted = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertFalse(json.loads(accepted.stdout)["native_acceptance_verified"])

    def test_enabled_states_with_different_keys_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.bundle(root, (1, 1), b"q" * 16)
            report = inspect_bundle_state(root)
            self.assertEqual(report["status"], "key_header_mismatch")
            self.assertFalse(report["stored_locally_enabled"])
            self.assertNotIn("q" * 16, json.dumps(report))

    def test_missing_corrupt_and_transitional_are_distinct_from_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(inspect_bundle_state(root)["status"], "missing_files")
            self.bundle(root, (2, 2))
            report = inspect_bundle_state(root)
            self.assertEqual(report["status"], "transitional_or_unknown_state")
            self.assertFalse(report["stored_locally_enabled"])
            (root / "Library.musicdb").write_bytes(b"unsupported")
            self.assertEqual(inspect_bundle_state(root)["status"], "unsupported")
            self.bundle(root, (0, 0))
            self.assertEqual(inspect_bundle_state(root)["status"], "stored_disabled")


if __name__ == "__main__":
    unittest.main()
