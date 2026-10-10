"""Rebind old-map seed-video hints through a typed identity-map transition."""
import argparse
import hashlib
import json
from pathlib import Path

from seed_video_evidence import build_rebound_seed_video_evidence


def receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--old-identity-map', type=Path, required=True)
    parser.add_argument('--current-identity-map', type=Path, required=True)
    parser.add_argument('--transition-graph-export', type=Path, required=True)
    parser.add_argument('--transition-report', type=Path, required=True)
    parser.add_argument('--transition-state', type=Path, required=True)
    parser.add_argument('--catalog-evidence', type=Path,
                        help='Prior strict catalog or combined seed evidence to rematch')
    parser.add_argument('--cached-hint-evidence', type=Path,
                        help='Prior typed cached-search rematch artifact')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Output exists; choose a new path')
    transition = {
        'old_identity_map': receipt(args.old_identity_map),
        'new_identity_map': receipt(args.current_identity_map),
        'graph_export': receipt(args.transition_graph_export),
        'rebase_report': receipt(args.transition_report),
        'state': receipt(args.transition_state),
    }
    result = build_rebound_seed_video_evidence(
        track_snapshot=args.track_snapshot,
        old_identity_map=args.old_identity_map,
        current_identity_map=args.current_identity_map,
        typed_transition=transition,
        catalog_evidence=args.catalog_evidence,
        cached_hint_evidence=args.cached_hint_evidence)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), **result['audit']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
