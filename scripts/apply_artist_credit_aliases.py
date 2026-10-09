"""Apply explicitly reviewed whole-credit aliases to a library identity map."""
import argparse
import hashlib
import json
from pathlib import Path

from music_identity_map import apply_artist_credit_alias_review, load_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', required=True, type=Path)
    parser.add_argument('--identity-map', required=True, type=Path)
    parser.add_argument('--proposals', required=True, type=Path)
    parser.add_argument('--review-decisions', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    inputs = [args.track_snapshot.resolve(), args.identity_map.resolve(),
              args.proposals.resolve(), args.review_decisions.resolve()]
    output, receipt = args.output.resolve(), args.receipt.resolve()
    if output in inputs or receipt in inputs or output == receipt or output.exists() or receipt.exists():
        parser.error('outputs must be new paths outside all inputs')
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    proposal_bytes = args.proposals.read_bytes()
    decision_bytes = args.review_decisions.read_bytes()
    document = json.loads(map_bytes)
    proposals = json.loads(proposal_bytes)
    decisions = json.loads(decision_bytes)
    source_report = proposals.get('source_report') or {}
    source_report_path = Path(source_report.get('path') or '')
    if not source_report_path.is_file() or hashlib.sha256(source_report_path.read_bytes()).hexdigest() != source_report.get('sha256'):
        raise ValueError('Proposal source report is missing or has changed')
    map_hash = hashlib.sha256(map_bytes).hexdigest()
    proposal_hash = hashlib.sha256(proposal_bytes).hexdigest()
    result = apply_artist_credit_alias_review(
        document, decisions, proposals, tracks, library_hash, map_hash, proposal_hash)
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt_document = {
        'schema_version': 1,
        'operation': 'apply_reviewed_whole_artist_credit_aliases',
        'library_sha256': library_hash,
        'input_identity_map_sha256': map_hash,
        'output_identity_map_sha256': output_hash,
        'proposal_sha256': proposal_hash,
        'source_report_sha256': proposals['source_report']['sha256'],
        'review_decisions_sha256': hashlib.sha256(decision_bytes).hexdigest(),
        'activated_aliases': len(decisions['decisions']),
        'recording_identity_verified': False,
    }
    with receipt.open('x', encoding='utf-8') as handle:
        json.dump(receipt_document, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'receipt': str(receipt), **receipt_document}))


if __name__ == '__main__':
    main()
