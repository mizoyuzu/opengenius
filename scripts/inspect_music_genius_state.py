"""Read both persisted Genius state bytes; no Music process or network needed.

An enabled stored pair does not prove native Music or iPod acceptance. The
input should be a consistent copy taken after Music and its library agent exit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zlib

from prepare_music_genius_state import STATE_FILES, inspect_state_pair


def inspect_bundle_state(bundle):
    bundle = Path(bundle)
    report = {"schema_version": 1, "status": "unsupported", "files": {},
              "stored_locally_enabled": False, "state_pair": None,
              "native_acceptance_verified": False, "ipod_acceptance_verified": False}
    missing = [name for name in STATE_FILES if not (bundle / name).is_file()]
    if missing:
        report.update(status="missing_files", missing_files=missing)
        return report
    blobs = {}
    for name in STATE_FILES:
        raw = (bundle / name).read_bytes()
        blobs[name] = raw
        report["files"][name] = {"size_bytes": len(raw),
                                 "sha256": hashlib.sha256(raw).hexdigest()}
    try:
        pair = inspect_state_pair(blobs)
    except (ValueError, zlib.error):
        # Unknown profiles are not a disabled state. Avoid leaking parser
        # exception text, which could contain data from an untrusted input.
        report["status"] = "unsupported"
        return report
    report["state_pair"] = pair
    if not pair["key_headers_match"]:
        report["status"] = "key_header_mismatch"
    elif not pair["states_match"]:
        report["status"] = "state_mismatch"
    elif pair["locally_enabled"]:
        report.update(status="stored_enabled", stored_locally_enabled=True)
    elif all(state == 0 for state in pair["states"].values()):
        report["status"] = "stored_disabled"
    else:
        report["status"] = "transitional_or_unknown_state"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--require-enabled", action="store_true",
                        help="Exit 2 unless both stored states are 1 with matching key headers")
    args = parser.parse_args()
    if not args.bundle.is_dir():
        parser.error("bundle must be a library directory or its copy")
    try:
        report = inspect_bundle_state(args.bundle)
    except OSError:
        parser.exit(1, "Could not read the library copy\n")
    print(json.dumps(report, indent=2))
    if args.require_enabled and not report["stored_locally_enabled"]:
        parser.exit(2)


if __name__ == "__main__":
    main()
