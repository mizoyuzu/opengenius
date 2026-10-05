"""Reproduce the read-only Genius state investigation for Music 1.7.0.146.

This is an address-pinned research extract, not a general vtable resolver.
It accepts only the inspected executable and never opens library/account data.
"""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

from inspect_music_macho import MachO


SHA256 = "a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a"
FUNCTIONS = {
    "cloud_manager_construction": 0x10028A160,
    "cloud_startup": 0x10028AC4C,
    "shutdown_cleanup": 0x10028EDD4,
    "account_state_import": 0x100290CE0,
    "opt_in": 0x100298CE4,
    "opt_out": 0x100299250,
    "opt_in_completion": 0x10029993C,
    "cloud_enabled": 0x1002A50FC,
    "cloud_state_predicate": 0x1002A77FC,
    "is_genius_opted_in": 0x1007BE81C,
    "serialize_preferences": 0x1000D66A8,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    macho = MachO(args.executable)
    if hashlib.sha256(macho.data).hexdigest() != SHA256:
        parser.error("Executable does not match the inspected Music 1.7.0.146")
    # Constructor 0x10028a248..264 installs this primary vtable. The startup
    # call uses +0x78. This authenticated rebase's low 32 bits are the offset
    # in this pinned image; no claim is made about other pointer formats.
    vtable = 0x101F15708
    pointer = macho.unpack("Q", macho.file_offset(vtable + 0x78))[0]
    target = 0x100000000 + (pointer & 0xFFFFFFFF)
    if not pointer & (1 << 63) or target != FUNCTIONS["opt_out"]:
        raise ValueError("Unexpected pinned virtual call target")
    args.output.mkdir(parents=True, exist_ok=True)
    for name, address in FUNCTIONS.items():
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            macho.disassemble(address)
        (args.output / f"{name}.txt").write_text(captured.getvalue())
    report = {
        "version": "1.7.0.146",
        "sha256": SHA256,
        "addresses_are_unslid": True,
        "functions": {name: hex(a) for name, a in FUNCTIONS.items()},
        "startup_virtual_call": {
            "vtable": hex(vtable), "slot": "0x78", "target": hex(target),
            "meaning": "DoGeniusOptOut; returns status, not an eligibility boolean",
        },
        "runtime_reset_cause_confirmed": False,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
