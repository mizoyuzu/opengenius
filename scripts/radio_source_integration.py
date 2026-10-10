"""Build durable proof-backed snapshots for the authenticated radio policy."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from radio_snapshot_proof import (
    POLICY, RadioSnapshotIneligible, _proof_from_inputs,
    create_radio_snapshot_proof,
    radio_only_snapshot_from_proof,
    verify_radio_snapshot_proof,
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write_new(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _radio_cache_path(snapshot, collector_dir):
    requests = [row for row in snapshot.get('request_evidence', [])
                if isinstance(row, dict)
                and row.get('endpoint') == 'get_watch_playlist'
                and isinstance(row.get('arguments'), dict)
                and row['arguments'].get('radio') is True]
    if len(requests) != 1:
        raise ValueError('Radio-only source snapshot lacks one exact radio request receipt')
    row = requests[0]
    encoded = json.dumps(['get_watch_playlist', row['arguments']],
                         sort_keys=True, ensure_ascii=False).encode('utf-8')
    digest = _sha(encoded)
    path = Path(collector_dir) / 'requests' / (digest + '.json')
    if not path.is_file():
        raise ValueError('Raw live radio response cache is missing')
    return path


def prepare_radio_only_snapshots(*, track_snapshot_path, identity_map_path,
                                 batch_dir, collector_summary_path,
                                 collector_seed_manifest_path, snapshot_paths,
                                 output_dir=None, sealed_at_utc=None,
                                 create_missing=True):
    """Create or replay immutable radio proofs and their sanitized snapshots.

    Original collector snapshots and caches are never modified. Existing proof
    sidecars are replayed exactly; partial sidecar sets fail closed.
    """
    batch_dir = Path(batch_dir).resolve()
    intent_path = batch_dir / 'intent.json'
    summary_path = Path(collector_summary_path).resolve()
    manifest_path = Path(collector_seed_manifest_path).resolve()
    output_dir = Path(output_dir or (batch_dir / 'radio-only-source')).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if sealed_at_utc is None:
        sealed_at_utc = datetime.now(timezone.utc).isoformat()
    proofs, snapshots, ineligible = [], [], []
    for original_path in sorted(Path(path).resolve() for path in snapshot_paths):
        original = _read(original_path)
        pid = original.get('local_seed', {}).get('persistent_id')
        if not isinstance(pid, str) or original_path.stem != pid:
            raise ValueError('Radio-only snapshot path differs from its authenticated seed PID')
        proof_path = output_dir / (pid + '.proof.json')
        sanitized_path = output_dir / (pid + '.snapshot.json')
        cache_path = _radio_cache_path(original, original_path.parent)
        try:
            if proof_path.exists():
                proof = _read(proof_path)
            else:
                if not create_missing:
                    if sanitized_path.exists():
                        raise ValueError(
                            'A sanitized radio snapshot exists without its proof sidecar')
                    # Re-run the complete source replay without creating durable
                    # output. A typed ineligible result is itself reproducible
                    # provenance; a proof-eligible input with no recorded proof
                    # remains an integrity failure.
                    _proof_from_inputs(
                        track_snapshot_path=track_snapshot_path,
                        identity_map_path=identity_map_path,
                        snapshot_path=original_path,
                        collector_summary_path=summary_path,
                        collector_seed_manifest_path=manifest_path,
                        batch_intent_path=intent_path,
                        raw_radio_cache_path=cache_path,
                        sealed_at_utc=sealed_at_utc)
                    raise ValueError('A recorded radio proof sidecar is missing')
                proof = create_radio_snapshot_proof(
                    track_snapshot_path=track_snapshot_path,
                    identity_map_path=identity_map_path,
                    snapshot_path=original_path,
                    collector_summary_path=summary_path,
                    collector_seed_manifest_path=manifest_path,
                    batch_intent_path=intent_path,
                    raw_radio_cache_path=cache_path,
                    output_path=proof_path,
                    sealed_at_utc=sealed_at_utc)
        except RadioSnapshotIneligible as error:
            ineligible.append({'persistent_id': pid,
                               'collector_snapshot': {
                                   'path': str(original_path),
                                   'sha256': _sha(original_path.read_bytes())},
                               'raw_radio_cache': {
                                   'path': str(cache_path),
                                   'sha256': _sha(cache_path.read_bytes())},
                               'reason': error.reason})
            continue
        validation = verify_radio_snapshot_proof(proof_path)
        if (validation.get('verified') is not True
                or proof.get('selected_seed', {}).get('persistent_id') != pid
                or validation.get('collector_snapshot_sha256') != _sha(original_path.read_bytes())):
            raise ValueError('Radio proof differs from its original collector snapshot')
        sanitized = radio_only_snapshot_from_proof(proof_path)
        if sanitized_path.exists():
            saved = _read(sanitized_path)
            if saved != sanitized:
                raise ValueError('Saved radio-only snapshot differs from proof replay')
        else:
            if not create_missing:
                raise ValueError('A recorded sanitized radio snapshot is missing')
            _write_new(sanitized_path, sanitized)
        receipt = {'path': str(sanitized_path), 'sha256': _sha(sanitized_path.read_bytes())}
        proof_receipt = {'path': str(proof_path), 'sha256': _sha(proof_path.read_bytes())}
        if (sanitized.get('radio_only_source_proof') != proof_receipt
                or proof.get('raw_radio_cache', {}).get('sha256') is None):
            raise ValueError('Sanitized radio snapshot does not bind its source proof')
        proofs.append(proof_receipt)
        snapshots.append(receipt)
    return {'source_policy': POLICY, 'proofs': proofs, 'snapshot_inputs': snapshots,
            'ineligible': ineligible, 'output_dir': str(output_dir)}
