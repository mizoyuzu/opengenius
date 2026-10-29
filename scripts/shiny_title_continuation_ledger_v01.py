"""Exact accounting across the accepted radio batch and reviewed title child."""
import hashlib
import json
from pathlib import Path

ROOT = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
CAMPAIGN = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v14-source-union-live'
METADATA = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02'
CHILD = ROOT / 'data/ytmusic/shiny-ost-title-alias-growth-child-20261014-v01'
OLD_MAP_HASH = '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67'
NEW_MAP_HASH = 'f4ed5d23c1a6b2909defa135387867937f0133e550fe1db0c30a278e5fda4734'
LIBRARY_HASH = '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7'
SNAPSHOT_HASH = '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f'
KEYS = {'schema_version', 'producer', 'parent_progress', 'parent_normal_terminal',
        'metadata_summary', 'metadata_intent', 'child_normal_terminal',
        'excluded_persistent_ids', 'http_requests_total', 'base_outgoing_roots'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned(path, expected):
    require(sha(path) == expected, 'Pinned continuation input changed: ' + str(path))
    return json.loads(Path(path).read_text())


def accepted_parent():
    terminal = pinned(CAMPAIGN / 'v09-replay-terminal.json',
                      'd0935b9fb2c5c831f8c6a4cc6be50a27e9ea437cb66a816bfcc02d84a971f2be')
    progress = pinned(CAMPAIGN / 'progress.json',
                      '89161c70cf75292e13edaaa34a1c3494bc4151af162c51f8b215b443734170e8')
    require(terminal.get('status') == 'completed' and terminal.get('returncode') == 0
            and terminal.get('roots') == 782 and terminal.get('http_requests') == 3992
            and terminal.get('attempted_pids') == 1837 and terminal.get('new_http_requests') == 0
            and terminal.get('progress_sha256') == sha(CAMPAIGN / 'progress.json')
            and terminal.get('runtime_manifest_sha256') ==
            '29e1336c39be927840fee071a1d40dfb862d4819ed49df1d473a92b013b44ebe',
            'Accepted radio parent terminal has another scope')
    pids = progress.get('attempted_pids')
    require(progress.get('status') == 'paused_at_checkpoint'
            and progress.get('successful_outgoing_roots') == 782
            and progress.get('http_requests') == 3992
            and isinstance(pids, list) and len(pids) == len(set(pids)) == 1837
            and len(progress.get('batches', [])) == 1,
            'Accepted radio parent accounting changed')
    from v14_source_union_attempt_ledger_v1 import validate as prior_validate
    prior = prior_validate(ROOT / 'data/ytmusic/v14-source-union-attempt-ledger-v01.json',
                           LIBRARY_HASH, SNAPSHOT_HASH, OLD_MAP_HASH)
    batch = progress['batches'][0]
    attempted = set(batch.get('attempted_pids', []))
    require(len(attempted) == 14 and not attempted & prior['attempted_pids']
            and set(pids) == prior['attempted_pids'] | attempted
            and batch.get('http_requests') == 44
            and batch.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and batch.get('outcome') == 'merged'
            and batch.get('growth_receipt', {}).get('sha256') ==
            '856c83333aad80d7159d3f1e02de1b043a9e1db8ce84e33fdd0790ec143d459f',
            'Radio parent does not preserve the exact earlier ledger and fourteen new attempts')
    return set(pids), prior['same_map_attempted_pids'] | attempted


def metadata_calls():
    report = pinned(METADATA / 'summary.json',
                    '97021f8ed910a53973aa8d32da5012a1cdacd09369f9b3d1c62fed98fe701628')
    intent = pinned(METADATA / 'intent.json',
                    '50a39e4a902761b1224c608ba32aeb64114a04a87e4f6a38aafaeb69e98d80b9')
    require(report.get('status') == 'completed' and report.get('http_requests') == 5
            and report.get('roots_credited') == 0 and report.get('attempted_pids') == 0
            and report.get('recommendation_edges_created') == 0
            and intent.get('maximum_http_requests') == 5 and intent.get('retries') == 0,
            'Catalog metadata accounting changed')
    events = report.get('request_events', [])
    require(len(events) == 5 and all(e.get('status') == 'completed'
            and e.get('status_code') == 200 for e in events)
            and [e.get('path') for e in events] == ['/', '/youtubei/v1/account/account_menu',
                '/youtubei/v1/browse', '/youtubei/v1/browse', '/youtubei/v1/browse'],
            'Catalog request transport sequence changed')
    require([a.get('album_id') for a in report['albums']] == intent['album_ids'],
            'Catalog result IDs differ from the declared requests')
    for album in report['albums']:
        pinned(Path(album['path']), album['sha256'])
    return 5


def validate(path, library_hash, snapshot_hash, current_identity_map_sha256=None):
    ledger = json.loads(Path(path).read_text())
    require(isinstance(ledger, dict) and set(ledger) == KEYS
            and ledger.get('schema_version') == 6
            and ledger.get('producer') == 'opengenius-reviewed-title-child-attempt-ledger-v1'
            and library_hash == LIBRARY_HASH and snapshot_hash == SNAPSHOT_HASH
            and current_identity_map_sha256 == NEW_MAP_HASH,
            'Reviewed title continuation ledger has another schema or Library')
    for name, target in [('parent_progress', CAMPAIGN / 'progress.json'),
                         ('parent_normal_terminal', CAMPAIGN / 'v09-replay-terminal.json'),
                         ('metadata_summary', METADATA / 'summary.json'),
                         ('metadata_intent', METADATA / 'intent.json'),
                         ('child_normal_terminal', CHILD / 'full-replay-terminal-v01.json')]:
        receipt = ledger[name]
        require(isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'}
                and receipt['path'] == str(target) and sha(target) == receipt['sha256'],
                'Title continuation receipt differs: ' + name)
    attempted, _ = accepted_parent()
    calls = metadata_calls()
    child = json.loads((CHILD / 'full-replay-terminal-v01.json').read_text())
    require(child.get('status') == 'normal_typed_child_consumer_pass'
            and child.get('returncode') == 0 and child.get('roots') == 782
            and child.get('assigned_rows') == 972 and child.get('new_http_requests') == 0
            and child.get('state_sha256') ==
            'd2efd3b944c6bf0ecf6cbb98a575a3d4d6e2b54c61efda3d384076ac0d754c0a'
            and child.get('runtime_manifest_sha256') ==
            '5623c0e3bf6390cb401557d327b1e6b511629fbdcea5315d00b0bb53467c1c39',
            'Reviewed title child has not completed its normal full replay')
    for name in ['accepted-baseline-graphs.json', 'identity-map-rebinding-report.json', 'state.json']:
        pinned(CHILD / name, child['output_hashes'][name])
    require(ledger['excluded_persistent_ids'] == sorted(attempted)
            and ledger['http_requests_total'] == 3992 + calls
            and ledger['base_outgoing_roots'] == 782,
            'Title continuation counters or excluded PIDs changed')
    return {'attempted_pids': attempted, 'same_map_attempted_pids': set(),
            'http_requests': 3997, 'diagnostic_attempted_pids': set(),
            'diagnostic_http_requests': calls, 'outgoing_roots': 782}
