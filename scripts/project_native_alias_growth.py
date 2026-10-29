"""Build a typed zero-call radio-growth checkpoint from a verified v13 alias child."""
import argparse
import json
from pathlib import Path

from merge_rebased_radio_growth import create_native_alias_projection_checkpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--baseline-state', type=Path, required=True)
    parser.add_argument('--baseline-graph-export', type=Path, required=True)
    parser.add_argument('--rebase-report', type=Path, required=True)
    parser.add_argument('--typed-alias-child', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = create_native_alias_projection_checkpoint(
        track_snapshot=args.track_snapshot,
        identity_map_path=args.identity_map,
        baseline_state_path=args.baseline_state,
        baseline_graph_export_path=args.baseline_graph_export,
        rebase_report_path=args.rebase_report,
        child_dir=args.typed_alias_child,
        output_dir=args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
