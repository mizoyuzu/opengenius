"""Replay a later work-radio stage against an explicitly reviewed capture profile.

Playlist metadata is discovery evidence only. The underlying auditor still
requires each proposed root's fresh authenticated radio and seed receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import audit_work_video_radio as audit


def checked_profile(path: Path, expected_sha: str) -> dict:
    raw = path.read_bytes()
    audit._fail(hashlib.sha256(raw).hexdigest() == expected_sha,
                'reviewed capture profile hash differs')
    profile = json.loads(raw)
    audit._fail(profile.get('kind') == 'root-reviewed-work-radio-capture-v1'
                and profile.get('root_credit') == 0
                and profile.get('database_written') is False,
                'profile is not a reviewed candidate-only capture')
    audit._fail(type(profile.get('http_before')) is int
                and 5130 <= profile['http_before'] <= 6300
                and profile.get('http_cap') == 6500,
                'reviewed profile exceeds cumulative campaign budget')
    for key in ('runtime_manifest_sha256', 'wrapper_sha256'):
        value = profile.get(key)
        audit._fail(isinstance(value, str) and len(value) == 64
                    and all(c in '0123456789abcdef' for c in value),
                    'reviewed profile has a malformed hash pin')
    for key in ('root', 'runtime', 'wrapper', 'stage', 'supervisor'):
        audit._fail(isinstance(profile.get(key), str)
                    and Path(profile[key]).is_absolute(),
                    'reviewed profile requires absolute paths')
    config = audit._wrapper_config(Path(profile['wrapper']))
    audit._fail(config.get('input_pins') == profile.get('capture_input_pins')
                and isinstance(profile.get('capture_input_pins'), dict)
                and bool(profile['capture_input_pins']),
                'reviewed profile and capture input pins differ')
    return profile


def run(profile_path: Path, expected_sha: str, output: Path,
        allow_completed_child_without_supervisor_terminal: bool = False):
    profile = checked_profile(profile_path, expected_sha)
    audit.EXPECTED_RUNTIME_MANIFEST_SHA256 = profile['runtime_manifest_sha256']
    audit.EXPECTED_WRAPPER_SHA256 = profile['wrapper_sha256']
    audit.EXPECTED_HTTP_BEFORE = profile['http_before']
    audit.EXPECTED_RUNTIME_MODULE_COUNT = profile.get('runtime_module_count', 187)
    audit._fail(audit.EXPECTED_RUNTIME_MODULE_COUNT in (187, 188),
                'reviewed capture runtime size differs from supported profiles')
    receipt = audit.run_audit(
        Path(profile['stage']), Path(profile['supervisor']),
        Path(profile['runtime']), Path(profile['wrapper']),
        Path(profile['root']), output,
        allow_completed_child_without_supervisor_terminal)
    return {'status': receipt['status'], 'summary': receipt['summary'],
            'http_cumulative': receipt['http_cumulative'],
            'reviewed_profile_sha256': expected_sha,
            'supervisor_recovery': receipt.get('supervisor_recovery'),
            'root_credit': 0, 'database_written': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed-profile', required=True, type=Path)
    parser.add_argument('--profile-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--allow-completed-child-without-supervisor-terminal',
                        action='store_true',
                        help='use independently verified completed worker receipts when the original supervisor terminal is absent')
    args = parser.parse_args()
    print(json.dumps(run(args.reviewed_profile, args.profile_sha256, args.output,
                         args.allow_completed_child_without_supervisor_terminal),
                     ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
