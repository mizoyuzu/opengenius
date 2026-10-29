"""Independently replay a final YT Music campaign under the pinned typed-union runtime."""
import argparse
import copy
import hashlib
import inspect
import importlib.util
import json
import os
import ast
from pathlib import Path
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v15-static-composed-absence-v01')
RUNTIME_MANIFEST = RUNTIME / 'runtime-manifest.json'
RUNTIME_PRODUCER = 'opengenius-final-campaign-runtime-v15-static-composed-absence-v01'
RUNTIME_MANIFEST_SHA256 = '83a888856e73f4fb6b51a897bcc96ccfbf17044c2b3d0f24b4e92ae4d0bcb47c'
UNION_ADAPTER = RUNTIME / 'scripts/project_radio_source_union_v05.py'
EXPECTED_V17_AUDITOR = ROOT / 'scripts/audit_final_ytmusic_source_v17.py'
EXPECTED_V17_AUDITOR_SHA256 = 'acf9becd36488ab55dc387b2591fb95a8cfd7a762d2cbd6c6621b1f4e2eb35e0'
EXPECTED_BASELINE_CHILD = ROOT / 'data/ytmusic/shiny-ost-title-alias-growth-child-20261014-v01'
ACCEPTED_PARENT_DIR = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v14-source-union-live'
ACCEPTED_PARENT_GROWTH = ACCEPTED_PARENT_DIR / 'batch-0001/growth'
ACCEPTED_SUFFIX_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v09-suffix-orphan-v01')
ACCEPTED_TITLE_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v10-title-origin-v01')
ACCEPTED_LEDGER_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v11-title-ledger-v01')
HISTORICAL_NATIVE_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v06')
HISTORICAL_NATIVE_MANIFEST_SHA256 = '2310e0eb13b21da42baebf5f806f71bda37490a3c3f9b228b901bc420acba5f5'
EXPECTED_PROGRESS_DIR = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v15-shiny-ost-radio-live-v01'
HISTORICAL_870_PROGRESS_SHA256 = 'cd4cd13ec7d58734854e5bde60c4a11832ee22cc55f2ac947ed703ee0b82ddf0'
HISTORICAL_870_GRAPH_SHA256 = '4d8045953eac5ce2885dcd545d96850b578e20420f1726e24a5c9ad718ae9935'
HISTORICAL_870_TERMINAL_SHA256 = 'fee44e5c45924697c4ee8c1498c4fc580fb783a6857719d7c18cd71d3c6b779e'
HISTORICAL_870_ROOT_COUNT = 870
PREPATCH_AUDITOR_ARCHIVE = ROOT / 'data/ytmusic/post870-historical-full-source-audit-20261015-v01/audit_final_ytmusic_source_v23_prepatch.py'
PREPATCH_AUDITOR_ARCHIVE_SHA256 = 'a43131adfca6cdfe1316adc97ae58af78f72aa9fcb8b91adff9dae91fe40d58a'
HISTORICAL_CODE_ARCHIVE = ROOT / 'data/ytmusic/post870-historical-full-source-audit-20261015-v01/historical-source-code-archive'
HISTORICAL_CODE_ARCHIVE_MANIFEST = HISTORICAL_CODE_ARCHIVE / 'manifest.json'
HISTORICAL_CODE_ARCHIVE_MANIFEST_SHA256 = 'e897f9d42be2acfad9ede8e00bdf7d7c15203f4c5e21dffdec27935a6b843f9e'
HISTORICAL_CODE_ARCHIVE_RECEIPTS = HISTORICAL_CODE_ARCHIVE / 'archive-source-receipts.json'
HISTORICAL_CODE_ARCHIVE_RECEIPTS_SHA256 = 'a8ce6f4cba5acc482114b51a5c5731d88e5344a2d726faafddbf2ab7e9916758'
V15_RECOVERY_RUNNER = Path('/tmp/opengenius-v15-recover-and-continue-v01.py')
V15_RECOVERY_RUNNER_SHA256 = '46b4d441c63fb4f86709cd11e580389a406537e5e855e945b8ded0e4665763f2'
V15_INVOCATION = EXPECTED_PROGRESS_DIR / 'v15-recovery-continuation-invocation-v01.json'
V15_PROCESS = EXPECTED_PROGRESS_DIR / 'v15-recovery-continuation-process-v01.json'
V15_TERMINAL = EXPECTED_PROGRESS_DIR / 'v15-recovery-continuation-terminal-v01.json'
V16_RECOVERY_RUNNER = Path('/tmp/opengenius-v16-recover-and-continue-v01.py')
V16_RECOVERY_RUNNER_SHA256 = '55c44ebd735a0b00e6b436af5ee0edda0d2dc5a9304dc922ac028a1457687ac4'
V16_INVOCATION = EXPECTED_PROGRESS_DIR / 'v16-recovery-continuation-invocation-v01.json'
V16_PROCESS = EXPECTED_PROGRESS_DIR / 'v16-recovery-continuation-process-v01.json'
V16_TERMINAL = EXPECTED_PROGRESS_DIR / 'v16-recovery-continuation-terminal-v01.json'
V16_RECOVERED_CHECKPOINT = EXPECTED_PROGRESS_DIR / 'v16-recovered840-progress-checkpoint-v01.json'
V16_PRESERVATION = EXPECTED_PROGRESS_DIR / 'v16-recovered840-preservation-before-api-v01.json'
V13_FAILED_TERMINAL = EXPECTED_PROGRESS_DIR / 'v13-continuation-supervisor-terminal-v05.json'
V13_FAILED_CHECKPOINT = EXPECTED_PROGRESS_DIR / 'v13-accepted826-progress-checkpoint-v01.json'
EXPECTED_MAP = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02/reviewed-shiny-ost-title-aliases-v01/identity-map.json'
HISTORICAL_SOURCE_MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
HISTORICAL_SOURCE_MAP_SHA256 = '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67'
V14_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v07-v14-ledger-v01')
V14_RUNTIME_MANIFEST = V14_RUNTIME / 'runtime-manifest.json'
V14_RUNTIME_MANIFEST_SHA256 = '6714294d83218eee79cb62bb854c621a6905fbb8e5db93b898e7c2301075f673'
EXPECTED_SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
EXPECTED_NATIVE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
EXPECTED_PROJECTION = Path('/tmp/opengenius-native-alias-projection-v06-v01')
EXPECTED_SOURCE_UNION = Path('/tmp/opengenius-radio-source-union-v06-candidate-v01')
PILOT_PROGRESS = ROOT / 'data/ytmusic/retrieval-query-pilot-20261013-v01/progress.json'
PILOT_PROGRESS_SHA256 = '1eea8551bdf70703c556b2273f36f921c2e99e4640f1ef69d33b41ff5c3ce5bf'
PILOT_PROFILE = PILOT_PROGRESS.parent / 'retrieval-profile.json'
PILOT_PROFILE_SHA256 = '56b67fd28d717d22e73db5396cc607b7cd83ad2bdee6de5fe347bf219191a0bb'
V14_SOURCE_UNION_DIR = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v14-source-union-live'
LINEAGE_PATH = V14_SOURCE_UNION_DIR / 'continuation-lineage.json'
LINEAGE_SHA256 = 'e762bfa3a5b838dc1e3a109900daad10cb4589fe350620dbf85ccf40f1095924'
V14_PROFILE = V14_SOURCE_UNION_DIR / 'profile.json'
V14_PROFILE_SHA256 = '940b53f1c2588dff1cff9be3ac2a6266c103f19a90f51be2bfd0919bf6b8aca9'
V14_INTENT = V14_SOURCE_UNION_DIR / 'intent-v02-v07-launch.json'
V14_INTENT_SHA256 = '73bab326b879818fe9f17c2b82b88a5623cbf0057a91b4c28301b63bfd8063ce'
V14_INVOCATION_PLAN = V14_SOURCE_UNION_DIR / 'campaign-invocation-plan-v02.json'
V14_INVOCATION_PLAN_SHA256 = '7dfe3a5c76c3bb37ad626734eefd62f0f5d020d07bb3f3fefff6a7e4e07024d7'
V14_SEED_PLAN = V14_SOURCE_UNION_DIR / 'seed-plan.json'
V14_SEED_PLAN_SHA256 = 'e70f06db5a7728de9667c1581d680d0c19f690e17d9c3232653004cdf4d11191'
V14_SEED_EVIDENCE = V14_SOURCE_UNION_DIR / 'seed-video-evidence.json'
V14_SEED_EVIDENCE_SHA256 = 'bca0e65760f4c206d9d61b1ff666976adf2578cc3be98a91a1db51ddf8d98863'
V14_PLAN_ONLY_SUMMARY = Path('/tmp/opengenius-song-for-prism-radio-pilot-v02/collector-plan/summary-1791935463545880895.json')
V14_PLAN_ONLY_SUMMARY_SHA256 = '127c7895b93a75186da0d834b3f9cc0d2a71a294bc07c7a6983248e53db06f3c'
V15_SEED_EVIDENCE = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02/standard-shiny-ost-seed-evidence-v01/seed-video-evidence.json'
V15_SEED_EVIDENCE_SHA256 = 'e7e0ad92f088b3c6e039abcdd4ca2e3310e24b2f7d5cbfda4aa38c505e5bddc7'
SOURCE_ORIGIN_INVENTORY = ROOT / 'data/ytmusic/root-source-origin-inventory-20261013-v01.json'
SOURCE_ORIGIN_INVENTORY_SHA256 = 'ecc9743c246949b5ddb3eca59491aacf4c6d9fa970ac7970058d75ad24523514'
CONTRACT_GROWTH = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261012-v09-radio-only-scaled-acquisition/live/batch-0011/growth'
EXPECTED = {
    'library': '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7',
    'snapshot': '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    'map': 'f4ed5d23c1a6b2909defa135387867937f0133e550fe1db0c30a278e5fda4734',
    'native_state_file': '477265d8936f6befa585a93b4826772815eea092872ff9c06d5571538a2eb4d7',
    'native_state_seal': '188e671a4d3d6743fd33d025a1684c831d1ddd9f87d72bae08250340eee6bd89',
    'native_graph': '0b0e12bce6644022ae921045bf9edfa9c4d8ab677bbc93d49070cd10c465e263',
    'native_report': '999e3d4cfb14485fcdbc05fd1f154d6ce1f7501390c62bf95a8a6d41002b6a08',
    'native_receipt': '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _canonical_document(value):
    """Serialize a complete persisted document without losing list order."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _require_final_progress_status(status):
    """Accept the frozen expander's real target terminal without rewriting it."""
    require(status in ('paused_at_checkpoint', 'target_reached', 'complete'),
            'final progress is not a frozen completed checkpoint')


def _validate_verified_radio_baseline_policy_transition(graph):
    """Independently bind the first radio run to the exact verified baseline.

    The normal campaign replay supplies ``baseline_rebase_provenance`` only
    after executing the trusted rebase verifier. This check independently
    verifies its file closure and the transition envelope; it does not credit
    any baseline PID as newly collected.
    """
    radio_policy = 'authenticated-watch-radio-only-v1'
    graph_path = EXPECTED_BASELINE_CHILD / 'accepted-baseline-graphs.json'
    report_path = EXPECTED_BASELINE_CHILD / 'identity-map-rebinding-report.json'
    state_path = EXPECTED_BASELINE_CHILD / 'state.json'
    pinned = {
        str(graph_path.resolve()): '0b0e12bce6644022ae921045bf9edfa9c4d8ab677bbc93d49070cd10c465e263',
        str(report_path.resolve()): '999e3d4cfb14485fcdbc05fd1f154d6ce1f7501390c62bf95a8a6d41002b6a08',
        str(state_path.resolve()): '477265d8936f6befa585a93b4826772815eea092872ff9c06d5571538a2eb4d7',
    }
    for path, expected_hash in pinned.items():
        require(Path(path).is_file() and sha(path) == expected_hash,
                'typed radio baseline artifact changed: ' + path)
    baseline_graph, baseline_report, baseline_state = map(load,
        (graph_path, report_path, state_path))
    provenance = graph.get('baseline_rebase_provenance')
    transition = graph.get('source_policy_transition')
    require(isinstance(provenance, dict) and isinstance(transition, dict),
            'final graph lacks verified radio-baseline provenance or transition')
    proof_inputs = provenance.get('proof_input_receipts')
    require(isinstance(proof_inputs, list) and proof_inputs,
            'verified radio baseline provenance lacks a proof-input closure')
    for receipt in proof_inputs:
        require(isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'}
                and isinstance(receipt.get('path'), str)
                and isinstance(receipt.get('sha256'), str)
                and Path(receipt['path']).is_file()
                and sha(receipt['path']) == receipt['sha256'],
                'verified radio baseline proof input changed')
    require(provenance.get('graph_export_path') == str(graph_path.resolve())
            and provenance.get('graph_export_sha256') == pinned[str(graph_path.resolve())]
            and provenance.get('rebase_report_path') == str(report_path.resolve())
            and provenance.get('rebase_report_sha256') == pinned[str(report_path.resolve())]
            and provenance.get('state_path') == str(state_path.resolve())
            and provenance.get('state_file_sha256') == pinned[str(state_path.resolve())],
            'baseline verifier receipt does not bind the exact typed child files')
    roots = baseline_graph.get('accepted_roots')
    require(isinstance(roots, list), 'typed radio baseline graph has no root rows')
    nonempty_count = sum(bool(row.get('ordered_target_pids')) for row in roots)
    edge_count = sum(len(row.get('ordered_target_pids', [])) for row in roots)
    state_rows = baseline_state.get('rows')
    require(len(roots) == 943 and isinstance(state_rows, list) and len(state_rows) == 972,
            'typed radio baseline must preserve the exact 943 graph rows and 972 assignments')
    require(baseline_graph.get('producer') == 'verified-identity-map-rebound-state-graph-export'
            and baseline_graph.get('source_policy') == radio_policy
            and baseline_graph.get('source_state_sha256') == pinned[str(state_path.resolve())]
            and baseline_report.get('producer') ==
            'opengenius-typed-identity-map-alias-chain-growth-rebinding-v1'
            and baseline_report.get('source_policy') == radio_policy
            and baseline_report.get('zero_id_library_sha256') == EXPECTED['library']
            and baseline_report.get('identity_map_sha256') == EXPECTED['map']
            and baseline_report.get('next_state_sha256') == baseline_state.get('state_sha256')
            and baseline_report.get('accepted_root_count') == nonempty_count == 782
            and baseline_report.get('directed_edge_count') == edge_count == 18271
            and baseline_state.get('state_sha256') == 'd2efd3b944c6bf0ecf6cbb98a575a3d4d6e2b54c61efda3d384076ac0d754c0a',
            'typed radio baseline graph, report, or state contract changed')
    require(graph.get('baseline_state_sha256') == baseline_state.get('state_sha256')
            and graph.get('baseline_accepted_roots') == roots,
            'final campaign baseline differs from the reviewed typed radio child')
    receipt_digest = hashlib.sha256(json.dumps(
        proof_inputs, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    expected_transition = {
        'schema_version': 1,
        'producer': 'opengenius-radio-verified-baseline-policy-inheritance-v1',
        'source_policy': radio_policy,
        'first_radio_run_index': 0,
        'baseline_graph_export': {
            'path': str(graph_path.resolve()),
            'sha256': pinned[str(graph_path.resolve())],
            'producer': baseline_graph['producer'],
            'accepted_pid_row_count': len(roots),
            'nonempty_root_count': nonempty_count,
            'directed_edge_count': edge_count,
        },
        'baseline_rebase_report': {
            'path': str(report_path.resolve()),
            'sha256': pinned[str(report_path.resolve())],
            'producer': baseline_report['producer'],
            'library_sha256': EXPECTED['library'],
            'identity_map_sha256': EXPECTED['map'],
        },
        'baseline_state': {
            'path': str(state_path.resolve()),
            'sha256': pinned[str(state_path.resolve())],
            'state_sha256': baseline_state['state_sha256'],
            'assignment_count': len(baseline_state['rows']),
        },
        'library_sha256': EXPECTED['library'],
        'identity_map_sha256': EXPECTED['map'],
        'proof_input_receipts_sha256': receipt_digest,
        'raw_observation_receipt_count': provenance['raw_observation_receipt_count'],
        'anchored_observation_count': provenance['anchored_observation_count'],
        'inheritance_http_requests': 0,
        'inheritance_attempt_delta': 0,
    }
    transition_bytes = json.dumps(transition, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':')).encode('utf-8')
    expected_bytes = json.dumps(expected_transition, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':')).encode('utf-8')
    require(transition_bytes == expected_bytes,
            'radio-baseline policy transition differs from its exact proof receipt')
    runs = graph.get('growth_runs')
    require(isinstance(runs, list) and runs
            and runs[0].get('source_policy') == radio_policy
            and runs[0].get('source_policy_transition') == transition
            and all(run.get('source_policy') == radio_policy for run in runs)
            and all('source_policy_transition' not in run for run in runs[1:]),
            'radio-baseline transition is not attached once to first radio run')
    return {
        'producer': transition['producer'],
        'baseline_nonempty_roots': nonempty_count,
        'baseline_edges': edge_count,
        'baseline_assignment_rows': len(baseline_state['rows']),
        'first_radio_run_index': 0,
        'source_credit_added_by_transition': 0,
        'http_requests_added_by_transition': 0,
    }


def _source_origin_inventory():
    require(SOURCE_ORIGIN_INVENTORY.is_file()
            and sha(SOURCE_ORIGIN_INVENTORY) == SOURCE_ORIGIN_INVENTORY_SHA256,
            'known source-origin inventory is missing or changed')
    inventory = load(SOURCE_ORIGIN_INVENTORY)
    require(inventory.get('producer') == 'opengenius-typed-root-source-origin-inventory-v1'
            and inventory.get('status') ==
            'source_inventory_structural_only_full_whole_source_replay_not_performed',
            'known source-origin inventory has an unexpected producer or scope')
    receipts = inventory.get('source_artifact_sha256')
    require(isinstance(receipts, dict) and len(receipts) >= 600,
            'known source-origin inventory lacks its historical source receipt closure')
    for source, expected_hash in receipts.items():
        path = Path(source)
        require(path.is_file() and isinstance(expected_hash, str) and sha(path) == expected_hash,
                'known historical source input changed: ' + source)
    sets = inventory.get('source_pid_sets')
    require(isinstance(sets, dict), 'known source-origin inventory lacks exact PID sets')
    native = set(sets.get('original_native_baseline_nonempty_root_pids', []))
    known_yt = set(sets.get('current_732_ytmusic_source_backed_root_pids', []))
    current_unknown = set(sets.get('current_732_unknown_baseline_root_pids', []))
    require(len(native) == 159 and len(known_yt) == 573 and not native & known_yt
            and not current_unknown,
            'known historical Native/YT source-origin PID sets are inconsistent')
    return inventory, receipts, native, known_yt


def _accepted_782_yt_origin_pids():
    """Add the accepted source-union and 14-root suffix after v17 verifies them."""
    require(EXPECTED_V17_AUDITOR.is_file()
            and sha(EXPECTED_V17_AUDITOR) == EXPECTED_V17_AUDITOR_SHA256,
            'fixed accepted-parent auditor changed')
    spec = importlib.util.spec_from_file_location('_accepted_parent_v17', EXPECTED_V17_AUDITOR)
    require(spec is not None and spec.loader is not None,
            'cannot load pinned accepted-parent auditor')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(Path(module.__file__).resolve() == EXPECTED_V17_AUDITOR.resolve(),
            'accepted-parent auditor loaded from unexpected path')
    # The accepted-parent auditor intentionally replays the older v12 runtime.
    # Clear overlapping top-level imports so this remains true even when callers
    # invoke the prior-generation helper in a test process that already loaded v13 modules.
    baseline_manifest = load(module.BASELINE_MANIFEST)
    legacy_names = {Path(row['path']).stem for row in baseline_manifest['modules']
                    if row['path'].endswith('.py')}
    prior_modules = {name: sys.modules.pop(name) for name in legacy_names
                     if name in sys.modules}
    try:
        parent_summary = module.verify_accepted_782_parent()
    finally:
        for name in legacy_names:
            loaded = sys.modules.get(name)
            loaded_path = getattr(loaded, '__file__', None)
            if loaded_path and Path(loaded_path).resolve().is_relative_to(module.BASELINE_RUNTIME.resolve()):
                sys.modules.pop(name, None)
        sys.modules.update(prior_modules)
    require(parent_summary['base_total_genuine_roots'] == 782
            and parent_summary['unknown_roots'] == 0,
            'accepted-parent source audit no longer verifies exact 782-root lineage')
    parent_graph = load(EXPECTED_PROGRESS_DIR.parent / 'automatic-genuine-roots-1000-20261014-v14-source-union-live/batch-0001/growth/accepted-graphs.json')
    runs = parent_graph.get('growth_runs')
    require(isinstance(runs, list) and len(runs) == 3,
            'accepted 782-root graph no longer contains typed union plus source suffix')
    typed_rows = runs[1].get('typed_source_origin_graphs')
    suffix_rows = runs[2].get('new_graphs')
    active = set(row['root_pid'] for row in parent_graph['accepted_roots']
                 if row['ordered_target_pids'])
    typed = _typed_active_root_pids(typed_rows, active)
    suffix = _typed_active_root_pids(suffix_rows, active)
    require(len(typed) == 609 and len(suffix) == 14 and not typed & suffix
            and len(typed | suffix) == 623,
            'accepted 782 YT source-origin PID vectors differ from typed 609+14 lineage')
    return typed | suffix


def _verify_v14_continuation_lineage():
    """Verify immutable pre-v14 counters, source union, and no-credit diagnostics."""
    require(V14_RUNTIME_MANIFEST.is_file()
            and sha(V14_RUNTIME_MANIFEST) == V14_RUNTIME_MANIFEST_SHA256,
            'v14 historical runtime manifest differs from its exact intent pin')
    v14_manifest = load(V14_RUNTIME_MANIFEST)
    require(v14_manifest.get('code_root') == str(V14_RUNTIME)
            and isinstance(v14_manifest.get('modules'), list)
            and len(v14_manifest['modules']) == 176,
            'v14 historical runtime manifest identity differs from its intent')
    for row in v14_manifest['modules']:
        relative = Path(row['path'])
        require(not relative.is_absolute() and '..' not in relative.parts,
                'v14 historical runtime manifest contains an escaping module path')
        module_path = V14_RUNTIME / relative
        require(module_path.is_file() and not module_path.is_symlink()
                and sha(module_path) == row['sha256'],
                'v14 historical runtime module differs from its frozen manifest: '
                + relative.as_posix())
    for path, expected in ((LINEAGE_PATH, LINEAGE_SHA256), (V14_PROFILE, V14_PROFILE_SHA256),
                           (V14_INTENT, V14_INTENT_SHA256),
                           (V14_INVOCATION_PLAN, V14_INVOCATION_PLAN_SHA256),
                           (V14_SEED_PLAN, V14_SEED_PLAN_SHA256),
                           (V14_SEED_EVIDENCE, V14_SEED_EVIDENCE_SHA256),
                           (V14_PLAN_ONLY_SUMMARY, V14_PLAN_ONLY_SUMMARY_SHA256)):
        require(path.is_file() and sha(path) == expected,
                'sealed v14 continuation input changed: ' + str(path))
    historical_intent = V14_SOURCE_UNION_DIR / 'intent.json'
    require(historical_intent.is_file() and sha(historical_intent) == 'd014b8c076cb24f9d3de5e922cd55524e102f1188ccbf97107d92cf69566377c',
            'historical preparatory intent changed')
    lineage = load(LINEAGE_PATH)
    require(lineage.get('producer') == 'opengenius-v14-source-union-continuation-lineage-v1'
            and lineage.get('status') == 'lineage_receipt_pending_collection_config_review'
            and lineage.get('campaign_progress_path') == str(V14_SOURCE_UNION_DIR / 'progress.json'),
            'v14 continuation lineage has unexpected producer, status, or progress target')
    counts = lineage.get('aggregate_before_v14')
    require(counts == {'accepted_nonempty_roots': 768, 'additional_retry_releases': [],
                       'attempted_pid_count': 1823, 'diagnostic_attempted_pids': 0,
                       'diagnostic_root_edge_credit': 0, 'prior_accounted_http': 3948},
            'v14 continuation aggregate counters differ from verified lineage')
    union = lineage.get('accepted_source_union')
    require(isinstance(union, dict) and union.get('accepted_roots') == 768
            and union.get('ordered_edges') == 18047 and union.get('growth_runs') == 2,
            'v14 source-union summary differs from accepted checkpoint')
    expected_union_receipts = {
        'accepted_graphs': (EXPECTED_SOURCE_UNION / 'accepted-graphs.json',
                            'f951fb462d2f31d4e3c95e83d99a7f5dfb63052f96015cf1f52d160a55e50789'),
        'state': (EXPECTED_SOURCE_UNION / 'state.json',
                  '69e5254f42580241c026426a3b7171f0790178af585bb49b941dcd3b45c93a4f'),
        'accepted_evaluation': (EXPECTED_SOURCE_UNION / 'accepted-evaluation.json',
                                '106519d95bb43b314b4992c60dcec3ec757baf4744f516481b657fdc112465a4'),
        'relations': (EXPECTED_SOURCE_UNION / 'relations.json',
                      '7eb09112f06999b7bca4b01703c3a7de7f617f8f47c6560c175eb6840fe329ea'),
        'projection_proof': (EXPECTED_SOURCE_UNION / 'projection-proof.json',
                             '7144ae28489a848bd104a664f2f80b0312f22633c7c017cb890e4a227eb3681a'),
        # The accepted source-union producer ran under v06. The v07 manifest
        # is the current continuation runtime and must remain a separate pin.
        'runtime_manifest': (Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v06-c6-cache-rematch-v01/runtime-manifest.json'),
                             'd2e005f3b711b2bc20877b9e5353d00c3af6ff4e2aa67f1bcdaf10ecddc7584f'),
        'normal_dispatch_consumer_return': (Path('/tmp/opengenius-radio-source-union-v06-proof-run-v01/normal-dispatch-consumer-return.json'),
                                            'e4fbde4cb2e8261e2d88e864cb5aa3eea8f6ca968e5fe0845df655b8acb6fb2e'),
        'terminal_status': (Path('/tmp/opengenius-radio-source-union-v06-proof-run-v01/terminal-status.json'),
                            'e47cd8805bdc2561685b4f0d9331ff8f3daa5de1d3234919fb64d18e8f7405b0'),
    }
    for key, (expected_path, expected_hash) in expected_union_receipts.items():
        receipt = union.get(key)
        require(isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'}
                and Path(receipt['path']).resolve() == expected_path.resolve()
                and receipt['sha256'] == expected_hash and expected_path.is_file()
                and sha(expected_path) == expected_hash,
                'v14 accepted source-union receipt changed: ' + key)
    graph = load(expected_union_receipts['accepted_graphs'][0])
    evaluation = load(expected_union_receipts['accepted_evaluation'][0])
    state = load(expected_union_receipts['state'][0])
    normal = load(expected_union_receipts['normal_dispatch_consumer_return'][0])
    terminal = load(expected_union_receipts['terminal_status'][0])
    roots = graph.get('accepted_roots')
    require(isinstance(roots, list) and sum(bool(r.get('ordered_target_pids')) for r in roots) == 768
            and sum(len(r.get('ordered_target_pids', [])) for r in roots) == 18047
            and evaluation.get('relation_rows') == roots
            and evaluation.get('virtual_state_sha256') == state.get('state_sha256')
            and normal.get('status') == 'normal_dispatch_consumer_pass'
            and terminal.get('child_returncode') == 0 and terminal.get('status') == 'completed',
            'v14 accepted union documents or independent consumer receipts disagree')
    diagnostics = lineage.get('diagnostics_no_credit')
    require(isinstance(diagnostics, list) and len(diagnostics) == 3
            and [row.get('http_requests') for row in diagnostics] == [2, 3, 8]
            and all(row.get('attempted_pids') == row.get('root_credit') == row.get('edge_credit') == 0
                    for row in diagnostics),
            'v14 diagnostic lineage no longer proves exactly thirteen no-credit requests')
    for row in diagnostics:
        for key, receipt in row.items():
            if isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'}:
                path = Path(receipt['path']).resolve()
                require(path.is_file() and sha(path) == receipt['sha256'],
                        'v14 diagnostic receipt changed: ' + str(path))
    profile = load(V14_PROFILE)
    intent = load(V14_INTENT)
    require(profile.get('producer') == 'opengenius-song-for-prism-radio-only-profile-v02'
            and profile.get('profile_id') == 'song-for-prism-current-map-direct-radio-20261014-v02'
            and len(profile.get('seeds', [])) == 14
            and profile.get('parent_growth') == {'path': str(EXPECTED_SOURCE_UNION),
                'sha256': 'f951fb462d2f31d4e3c95e83d99a7f5dfb63052f96015cf1f52d160a55e50789'},
            'v14 profile no longer binds the exact fourteen fresh source candidates')
    collection = profile.get('collection')
    require(collection == {'candidate_limit': 100, 'collection_mode': 'radio-only-v1',
            'credit_policy': {'authenticated_watch_radio_proof_required': True,
                'outgoing_roots_created_before_source_proof': 0,
                'recommendation_edges_from_catalog': 0,
                'recording_identity_verified_before_source_proof': False},
            'follow_on_batches': False, 'maximum_http_requests': 60,
            'maximum_watch_radio_requests_per_seed': 1, 'minimum_interval_seconds': 3.0,
            'request_cache_policy': 'fresh-unique-output-namespace-v1', 'retry_count': 0,
            'seed_count': 14, 'seed_query_fallback': False, 'seed_search': False,
            'seed_video_evidence_mode': 'typed-current-map-album-catalog-candidate',
            'source_policy': 'authenticated-watch-radio-only-v1', 'startup_requests_expected': 2},
            'v14 source collection limits or radio-only policy changed')
    require(intent.get('producer') == 'opengenius-v14-song-for-prism-collection-intent-v02'
            and intent.get('profile_sha256') == V14_PROFILE_SHA256
            and intent.get('continuation_lineage_sha256') == LINEAGE_SHA256
            and intent.get('network_requests_permitted') is False
            and intent.get('expected_counts_before_batch') == {'attempted_pids': 1823,
                'http_requests': 3948, 'outgoing_roots': 768},
            'v14 intent no longer binds the sealed, no-network continuation profile')
    expected_runtime = {
        'code_root': str(V14_RUNTIME),
        'collector_entrypoint': str(V14_RUNTIME / 'scripts/probe_ytmusic_batch.py'),
        'collector_sha256': '552724584cc6e0730417ec4bf3d9c95ada7790dddc7a3933325abad24abd44f0',
        'expansion_entrypoint': str(V14_RUNTIME / 'scripts/expand_genuine_roots.py'),
        'expansion_sha256': 'adcd3b27089fd8bb0ec53e28ef659442b8e5c87e576daad6db401267fbb30c8e',
        'ledger_adapter': str(V14_RUNTIME / 'scripts/v14_source_union_attempt_ledger_v1.py'),
        'ledger_adapter_sha256': '5b13555999c03a3749ad249c3814fd3c80b89439865fc63205135aeae1acd07b',
        'manifest_path': str(V14_RUNTIME_MANIFEST),
        'manifest_sha256': V14_RUNTIME_MANIFEST_SHA256,
        'merge_entrypoint': str(V14_RUNTIME / 'scripts/merge_rebased_radio_growth.py'),
        'merge_sha256': '445d37d72ba65e07c0b7bdf4eafacfa897afe3d45405cf9831012723970f24ad',
        'module_count': 176,
    }
    expected_argv = [
        str(ROOT / '.venv/bin/python'), str(V14_RUNTIME / 'scripts/expand_genuine_roots.py'),
        '--track-snapshot', str(EXPECTED_SNAPSHOT), '--identity-map', str(HISTORICAL_SOURCE_MAP),
        '--baseline-state', str(EXPECTED_NATIVE / 'baseline-state.json'),
        '--baseline-graph-export', str(EXPECTED_NATIVE / 'accepted-baseline-graphs.json'),
        '--rebase-report', str(EXPECTED_NATIVE / 'baseline-rebase-report.json'),
        '--previous-growth', str(EXPECTED_SOURCE_UNION), '--seed-video-evidence', str(V14_SEED_EVIDENCE),
        '--prior-attempt-ledger', str(ROOT / 'data/ytmusic/v14-source-union-attempt-ledger-v01.json'),
        '--output', str(V14_SOURCE_UNION_DIR), '--auth',
        str(ROOT / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'),
        '--target-outgoing-roots', '1000', '--global-request-budget', '6500', '--max-attempts', '2800',
        '--batch-size', '14', '--batch-request-budget', '60', '--candidate-limit', '100',
        '--interval', '3', '--source-policy', 'authenticated-watch-radio-only-v1',
        '--collector-collection-mode', 'radio-only-v1', '--stop-after-batches', '1',
    ]
    require(intent.get('runtime') == expected_runtime
            and intent.get('prior_attempt_ledger') == {
                'path': str(ROOT / 'data/ytmusic/v14-source-union-attempt-ledger-v01.json'),
                'sha256': '4657dcdaba4b84818993a5d28596fa58c2c49ed22e6424121f3e97c9e52cf856'}
            and intent.get('previous_growth') == {
                'path': str(EXPECTED_SOURCE_UNION),
                'sha256': 'f951fb462d2f31d4e3c95e83d99a7f5dfb63052f96015cf1f52d160a55e50789'},
            'v14 pre-run intent does not pin the v07 runtime, exact ledger, and accepted union parent')
    invocation = load(V14_INVOCATION_PLAN)
    require(invocation.get('producer') == 'opengenius-v14-reviewed-live-invocation-plan-v1'
            and invocation.get('status') == 'root_verified_prelaunch'
            and invocation.get('network_requests_permitted') is True
            and invocation.get('intent_path') == str(V14_INTENT)
            and invocation.get('argv') == expected_argv
            and invocation.get('selected_seed_count') == 14
            and invocation.get('runtime_manifest_sha256') == V14_RUNTIME_MANIFEST_SHA256
            and invocation.get('attempt_ledger_sha256') == '4657dcdaba4b84818993a5d28596fa58c2c49ed22e6424121f3e97c9e52cf856'
            and invocation.get('intent_sha256') == V14_INTENT_SHA256
            and invocation.get('expected_before_batch') == {
                'attempted_pids': 1823, 'http_requests': 3948, 'outgoing_roots': 768}
            and invocation.get('previous_growth') == {
                'path': str(EXPECTED_SOURCE_UNION),
                'accepted_graphs_sha256': 'f951fb462d2f31d4e3c95e83d99a7f5dfb63052f96015cf1f52d160a55e50789'}
            and invocation.get('caps') == {
                'batch_size': 14, 'max_http_requests': 60,
                'max_watch_radio_requests_per_seed': 1, 'minimum_interval_seconds': 3,
                'stop_after_batches': 1}
            and invocation.get('continuation_lineage_sha256') == LINEAGE_SHA256,
            'v14 invocation plan differs from the reviewed finite radio-only command')
    plan = load(V14_SEED_PLAN)
    evidence = load(V14_SEED_EVIDENCE)
    summary = load(V14_PLAN_ONLY_SUMMARY)
    plan_rows, evidence_rows, profile_rows = plan.get('seeds'), evidence.get('rows'), profile.get('seeds')
    require(isinstance(plan_rows, list) and isinstance(evidence_rows, list)
            and isinstance(profile_rows, list) and len(plan_rows) == len(evidence_rows) == len(profile_rows) == 14
            and summary.get('status') == 'planned' and summary.get('http_requests') == 0
            and summary.get('selected_seed_count') == 14,
            'v14 plan-only run is not an exact zero-request fourteen-seed plan')
    plan_pids = [row.get('persistent_id') for row in plan_rows]
    evidence_pids = [row.get('persistent_id') for row in evidence_rows]
    profile_pids = [row.get('persistent_id') for row in profile_rows]
    video_ids = [row.get('video_id') for row in evidence_rows]
    prior_attempted = set(load(PILOT_PROGRESS).get('attempted_pids', []))
    union_roots = load(EXPECTED_SOURCE_UNION / 'accepted-graphs.json').get('accepted_roots', [])
    union_active = {row.get('root_pid') for row in union_roots if row.get('ordered_target_pids')}
    require(plan_pids == evidence_pids == profile_pids and len(set(plan_pids)) == 14
            and all(isinstance(video, str) and video for video in video_ids)
            and len(set(video_ids)) == 14 and not set(plan_pids) & (prior_attempted | union_active)
            and evidence.get('identity_map_sha256') == HISTORICAL_SOURCE_MAP_SHA256
            and evidence.get('library_sha256') == EXPECTED['library'],
            'v14 direct-video seed rows are duplicated, attempted, active, or bound to another map/library')
    return {'path': str(LINEAGE_PATH), 'sha256': LINEAGE_SHA256,
            'invocation_plan_path': str(V14_INVOCATION_PLAN),
            'invocation_plan_sha256': V14_INVOCATION_PLAN_SHA256,
            'intent_path': str(V14_INTENT), 'intent_sha256': V14_INTENT_SHA256,
            'profile_path': str(V14_PROFILE), 'profile_sha256': V14_PROFILE_SHA256,
            'prior_http_requests': 3948, 'prior_attempted_pids': 1823,
            'accepted_roots': 768, 'diagnostic_http_requests': 13,
            'diagnostic_credit': 0}


def _verify_historical_pilot():
    """Pin the no-root 16-seed retrieval pilot as history, never as root credit."""
    require(PILOT_PROGRESS.is_file() and sha(PILOT_PROGRESS) == PILOT_PROGRESS_SHA256,
            'authoritative retrieval-pilot progress changed')
    require(PILOT_PROFILE.is_file() and sha(PILOT_PROFILE) == PILOT_PROFILE_SHA256,
            'authoritative retrieval-pilot profile changed')
    progress = load(PILOT_PROGRESS)
    profile = load(PILOT_PROFILE)
    require(progress.get('schema_version') == 1
            and progress.get('status') == 'paused_at_checkpoint'
            and progress.get('http_requests') == 3935
            and isinstance(progress.get('attempted_pids'), list)
            and len(progress['attempted_pids']) == 1823
            and len(set(progress['attempted_pids'])) == 1823
            and progress.get('successful_outgoing_roots') == 733,
            'authoritative retrieval-pilot checkpoint counters or status changed')
    require(profile.get('producer') == 'opengenius-retrieval-query-pilot-profile-v1'
            and profile.get('profile_id') == 'retrieval-pilot-20261013-v01'
            and profile.get('schema_version') == 1
            and profile.get('base_progress_sha256') ==
            'c523add8ee459cd8ad84efc7bd68b7d556b30c6cc7600fceb5ea6703625e45c8'
            and profile.get('base_accepted_graph_sha256') ==
            '937451668dda15b4ff20bc2e03a504c538e89c3c82cf2e617153a059e90c75fe'
            and profile.get('source_library_sha256') == EXPECTED['library']
            and profile.get('identity_map_sha256') == HISTORICAL_SOURCE_MAP_SHA256
            and sha(HISTORICAL_SOURCE_MAP) == HISTORICAL_SOURCE_MAP_SHA256
            and profile.get('batch_number') == 2
            and profile.get('http_request_cap') == 80,
            'retrieval-pilot profile no longer binds the accepted base and bounded experiment')
    batches = progress.get('batches')
    require(isinstance(batches, list) and len(batches) == 2,
            'retrieval-pilot checkpoint does not preserve both completed batches')
    expected_requests = (85, 18)
    union_attempts = set()
    for index, batch in enumerate(batches):
        require(batch.get('status') == 'completed'
                and batch.get('source_policy') == 'authenticated-watch-radio-only-v1'
                and type(batch.get('http_requests')) is int
                and batch['http_requests'] == expected_requests[index],
                'retrieval-pilot batch status, source policy, or request count changed')
        seeds = batch.get('seed_pids')
        attempted = batch.get('attempted_pids')
        expected_count = 80 if index == 0 else 16
        require(isinstance(seeds, list) and len(seeds) == expected_count
                and len(set(seeds)) == expected_count
                and isinstance(attempted, list) and set(attempted) == set(seeds),
                'retrieval-pilot batch seed and attempt receipts do not match')
        require(not union_attempts.intersection(seeds),
                'retrieval-pilot batches repeat attempted PIDs')
        union_attempts.update(seeds)
        require(batch.get('growth_receipt') == batches[0].get('growth_receipt'),
                'no-root pilot batch changed its parent accepted-growth graph')
    pilot_seeds = profile.get('seeds')
    require(isinstance(pilot_seeds, list) and len(pilot_seeds) == 16
            and batches[1]['seed_pids'] == [row.get('persistent_id') for row in pilot_seeds],
            'pilot batch PID order differs from its pinned 8+8 retrieval profile')
    arms = [row.get('query_arm') for row in pilot_seeds]
    require(arms.count('album_aware') == 8 and arms.count('baseline_control') == 8,
            'retrieval-pilot profile is not the exact eight/eight comparison')
    summary_receipt = batches[1].get('collector_summary')
    require(isinstance(summary_receipt, dict)
            and set(summary_receipt) == {'path', 'sha256', 'http_requests', 'request_budget'}
            and summary_receipt.get('http_requests') == 18
            and summary_receipt.get('request_budget') == 80,
            'pilot batch has no exact collector summary receipt')
    summary_path = Path(summary_receipt['path']).resolve()
    require(summary_path.is_file() and sha(summary_path) == summary_receipt['sha256'],
            'pilot batch collector summary changed')
    summary = load(summary_path)
    results = summary.get('results')
    profile_by_pid = {row.get('persistent_id'): row for row in pilot_seeds}
    search_receipts_match = isinstance(results, list) and all(
        isinstance(row.get('search_request_evidence'), dict)
        and row['search_request_evidence'].get('arguments', {}).get('query')
            == profile_by_pid.get(row.get('persistent_id'), {}).get('query')
        and row['search_request_evidence'].get('arguments', {}).get('filter') == 'songs'
        and row['search_request_evidence'].get('arguments', {}).get('limit') == 5
        and row['search_request_evidence'].get('cache_policy') == 'fresh-append-only-namespace-v1'
        and row['search_request_evidence'].get('cache_hit') is False
        for row in results if isinstance(row, dict))
    require(summary.get('account_authentication_verified') is True
            and summary.get('status') == 'completed'
            and summary.get('http_requests') == 18
            and isinstance(results, list) and len(results) == 16
            and [row.get('persistent_id') for row in results] == batches[1]['seed_pids']
            and search_receipts_match
            and all(row.get('status') == 'search_unresolved'
                    and type(row.get('candidate_video_ids')) is int
                    and row['candidate_video_ids'] >= 0
                    and row.get('title_fallback_attempted') is False
                    for row in results)
            and not batches[1].get('snapshots') and not batches[1].get('snapshot_inputs'),
            'pilot does not remain an authenticated, zero-root diagnostic')
    return {'path': str(PILOT_PROGRESS), 'sha256': PILOT_PROGRESS_SHA256,
            'profile_path': str(PILOT_PROFILE), 'profile_sha256': PILOT_PROFILE_SHA256,
            'batches': 2, 'http_requests': 18, 'attempted_pid_count': 16,
            'roots_credited': 0, 'collector_summary_sha256': summary_receipt['sha256']}


def _verified_run_source_pids(normalized_runs, final_active_pids):
    """Derive roots from replay-normalized authenticated source runs, never counters."""
    sourced = set()
    verified_run_count = 0
    source_union_seen = False
    for index, run in enumerate(normalized_runs):
        if run.get('producer') == 'opengenius-radio-typed-source-union-run-v1':
            require(not source_union_seen and index == 1,
                    'typed source-union origin must be the single second replay-normalized run')
            require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                    'typed source union lacks authenticated radio policy')
            manifest = load(RUNTIME_MANIFEST)
            manifest_modules = {row['path']: row['sha256'] for row in manifest['modules']}
            helper_path = UNION_ADAPTER.resolve()
            relative_helper = helper_path.relative_to(RUNTIME.resolve()).as_posix()
            require(relative_helper == 'scripts/project_radio_source_union_v05.py'
                    and manifest_modules.get(relative_helper) == sha(helper_path),
                    'typed source-origin consumer is outside the pinned runtime manifest')
            spec = importlib.util.spec_from_file_location(
                '_audit_trusted_project_radio_source_union_v05', helper_path)
            require(spec is not None and spec.loader is not None,
                    'typed source-origin consumer cannot be loaded from its pinned path')
            adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter)
            require(Path(adapter.__file__).resolve() == helper_path,
                    'typed source-origin consumer resolved outside the pinned runtime')
            # The frozen whole-chain dispatcher already rebuilt and source-verified
            # this transition inside the shared proof-cache scope. Bind the
            # normalized run to the pinned source documents without launching a
            # second 1,650-input source-proof walk.
            adapter._validate_saved_union_run_contract(run)
            typed_rows = run.get('typed_source_origin_graphs')
            require(isinstance(typed_rows, list),
                    'typed source union omits its source-origin graph rows')
            typed_pids = _typed_active_root_pids(typed_rows, final_active_pids)
            require(len(typed_pids) == 609,
                    'typed source-origin PIDs do not match the exact active v06 source set')
            sourced.update(typed_pids)
            source_union_seen = True
        graph_rows = run.get('new_graphs', [])
        if not graph_rows:
            # Unresolved or empty-but-verified collection batches earn no root
            # credit. The typed projection also has no source graphs and must
            # remain a zero-call transition.
            if run.get('producer') == 'opengenius-radio-native-alias-projection-run-v1':
                require(run.get('http_requests') == 0 and run.get('attempted_pid_delta') == 0,
                        'typed Native alias projection claims collection activity')
            else:
                require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                        f'empty final growth run {index} lacks authenticated radio source policy')
            continue
        require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                f'final v15 growth run {index} lacks authenticated radio source policy')
        snapshot_rows = run.get('snapshot_inputs')
        require(isinstance(snapshot_rows, list) and snapshot_rows,
                f'final v15 growth run {index} has no replayed snapshot receipts')
        snapshot_pids = set()
        rows_by_pid = {row.get('root_pid'): row for row in graph_rows if isinstance(row, dict)}
        for receipt in snapshot_rows:
            require(isinstance(receipt, dict), 'replayed source snapshot receipt is malformed')
            path = Path(receipt.get('path', '')).resolve()
            require(path.is_file() and sha(path) == receipt.get('sha256'),
                    'replayed source snapshot changed: ' + str(path))
            snapshot = load(path)
            seed = snapshot.get('local_seed')
            pid = seed.get('persistent_id') if isinstance(seed, dict) else None
            require(isinstance(pid, str) and pid,
                    'replayed source snapshot has no exact seed PID')
            verified_pid, targets, proof = _verify_snapshot_source_receipt(receipt, pid)
            require(verified_pid == pid and pid in rows_by_pid
                    and rows_by_pid[pid].get('ordered_target_pids') == targets,
                    'replayed radio proof does not match its exact accepted ordered vector')
            require(pid not in snapshot_pids, 'replayed source run repeats a snapshot PID')
            snapshot_pids.add(pid)
        run_roots = set()
        for row in graph_rows:
            require(isinstance(row, dict) and isinstance(row.get('root_pid'), str)
                    and isinstance(row.get('ordered_target_pids'), list),
                    'replayed source graph row is malformed')
            if row['ordered_target_pids']:
                run_roots.add(row['root_pid'])
        require(run_roots <= snapshot_pids,
                f'final source run {index} has a nonempty root without a same-PID authenticated snapshot')
        sourced.update(run_roots & final_active_pids)
        verified_run_count += 1
    return sourced, verified_run_count


def _typed_active_root_pids(rows, final_active_pids):
    """Count only unique typed source rows with nonempty ordered relations."""
    require(isinstance(rows, list), 'typed source-origin graph rows are malformed')
    seen = set()
    active = set()
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('root_pid'), str)
                and row['root_pid'] and isinstance(row.get('ordered_target_pids'), list),
                'typed source-origin graph row is malformed')
        pid = row['root_pid']
        require(pid not in seen, 'typed source-origin graph rows repeat a PID')
        seen.add(pid)
        edges = row['ordered_target_pids']
        require(all(isinstance(edge, str) and edge for edge in edges),
                'typed source-origin row contains malformed ordered relations')
        if edges:
            active.add(pid)
    require(active <= final_active_pids,
            'typed source-origin PIDs are not active in the final accepted graph')
    return active


def _active_root_pids(vectors):
    require(isinstance(vectors, dict), 'root vectors must be an object')
    return {pid for pid, edges in vectors.items()
            if isinstance(pid, str) and pid and isinstance(edges, list) and edges}


def _verify_snapshot_source_receipt(receipt, expected_pid):
    require(isinstance(receipt, dict)
            and set(receipt) == {'path', 'sha256', 'radio_only_source_proof'},
            'replayed source snapshot receipt is malformed')
    path = Path(receipt['path']).resolve()
    require(path.is_file() and sha(path) == receipt['sha256'],
            'replayed source snapshot changed: ' + str(path))
    snapshot = load(path)
    seed = snapshot.get('local_seed')
    pid = seed.get('persistent_id') if isinstance(seed, dict) else None
    require(pid == expected_pid and snapshot.get('account_authentication_verified') is True,
            'replayed source snapshot is not authenticated for the exact seed PID')
    proof_receipt = receipt.get('radio_only_source_proof')
    require(isinstance(proof_receipt, dict) and set(proof_receipt) == {'path', 'sha256'},
            'replayed source snapshot has no exact radio-proof receipt')
    proof_path = Path(proof_receipt['path']).resolve()
    require(proof_path.is_file() and sha(proof_path) == proof_receipt['sha256'],
            'replayed radio source proof changed: ' + str(proof_path))
    proof = load(proof_path)
    require(proof.get('producer') == 'opengenius-authenticated-radio-snapshot-proof-v1'
            and proof.get('policy') == 'authenticated-watch-radio-only-v1'
            and proof.get('recording_identity_verified') is False
            and proof.get('library_sha256') == EXPECTED['library'],
            'replayed source proof has unexpected producer, policy, or recording identity')
    identity_map = proof.get('identity_map')
    allowed_maps = {EXPECTED['map'], HISTORICAL_SOURCE_MAP_SHA256}
    require(isinstance(identity_map, dict) and identity_map.get('sha256') in allowed_maps,
            'replayed source proof is not bound to a reviewed campaign map')
    map_path = EXPECTED_MAP if identity_map['sha256'] == EXPECTED['map'] else HISTORICAL_SOURCE_MAP
    require(map_path.is_file() and sha(map_path) == identity_map['sha256']
            and identity_map.get('path') == str(map_path),
            'replayed source proof map receipt differs from its fixed path/hash')
    targets = proof.get('outgoing_target_pids')
    edges = proof.get('outgoing_recommendation_edges')
    selected = proof.get('selected_seed')
    radio = proof.get('radio_request')
    require(isinstance(targets, list) and targets
            and all(isinstance(target, str) and target for target in targets)
            and isinstance(edges, list) and len(edges) == len(targets)
            and all(isinstance(edge, dict)
                    and edge.get('source_pid') == expected_pid
                    and edge.get('target_pid') == target
                    and edge.get('source_relation') == 'authenticated_watch_radio'
                    and isinstance(edge.get('video_ids'), list) and edge['video_ids']
                    for edge, target in zip(edges, targets))
            and len(targets) == len(set(targets))
            and isinstance(selected, dict) and selected.get('persistent_id') == expected_pid
            and isinstance(selected.get('video_id'), str) and selected['video_id']
            and isinstance(radio, dict) and radio.get('endpoint') == 'get_watch_playlist'
            and radio.get('arguments', {}).get('radio') is True
            and radio.get('arguments', {}).get('videoId') == selected['video_id'],
            'replayed radio proof has no valid ordered outgoing relations')
    return pid, targets, proof


def _classify_final_source_roots(final_active_pids, native_pids, known_yt_pids, normalized_runs):
    run_yt_pids, run_count = _verified_run_source_pids(normalized_runs, final_active_pids)
    yt_pids = (known_yt_pids | run_yt_pids) & final_active_pids
    native_present = native_pids & final_active_pids
    overlap = native_present & yt_pids
    native_only = native_present - yt_pids
    unknown = final_active_pids - (native_only | yt_pids)
    require(not unknown,
            'final accepted graph contains active roots with no Native or replay-verified YT source origin')
    return {
        'native_only_pids': sorted(native_only),
        'ytmusic_source_backed_pids': sorted(yt_pids),
        'native_ytmusic_overlap_pids': sorted(overlap),
        'unknown_pids': sorted(unknown),
        'verified_final_growth_run_count': run_count,
        'counts': {'native_only': len(native_only), 'ytmusic_source_backed': len(yt_pids),
                   'native_ytmusic_overlap': len(overlap), 'unknown': len(unknown),
                   'total_active': len(final_active_pids)},
    }


def _goal_root_count(source_origin, goal_metric):
    require(goal_metric in ('ytmusic-backed', 'total-genuine'),
            'unsupported root-count goal metric')
    counts = source_origin['counts']
    require(counts['unknown'] == 0
            and counts['native_only'] + counts['ytmusic_source_backed'] == counts['total_active'],
            'source-origin PID sets do not form an exact final-root partition')
    return (counts['ytmusic_source_backed'] if goal_metric == 'ytmusic-backed'
            else counts['total_active'])


def _require_goal(source_origin, goal_metric):
    qualifying_count = _goal_root_count(source_origin, goal_metric)
    require(qualifying_count >= 1000,
            f'final checkpoint has fewer than 1000 roots for requested {goal_metric} metric: '
            f'{qualifying_count}')
    return qualifying_count


def _require_historical_870_partition(source_origin, goal_metric):
    count = _goal_root_count(source_origin, goal_metric)
    require(count == HISTORICAL_870_ROOT_COUNT
            and source_origin['counts']['native_only'] == 159
            and source_origin['counts']['ytmusic_source_backed'] == 711
            and source_origin['counts']['native_ytmusic_overlap'] == 0
            and source_origin['counts']['unknown'] == 0,
            'historical 870 replay does not resolve to the exact Native159 plus YT711 partition')
    return count


def _precheck_target(progress_path, goal_metric, historical_exhausted_870=False):
    """Fail fast below the target; counters never grant source credit."""
    progress_path = Path(progress_path).resolve()
    require(progress_path.parent == EXPECTED_PROGRESS_DIR.resolve(),
            'progress path is not in the designated v15 title-map acquisition output')
    progress = load(progress_path)
    require(goal_metric == 'total-genuine',
            'this campaign finalization target is total Native plus YTMusic roots')
    count = progress.get('successful_outgoing_roots')
    if historical_exhausted_870:
        require(sha(progress_path) == HISTORICAL_870_PROGRESS_SHA256
                and progress.get('status') == 'exhausted_candidates'
                and count == HISTORICAL_870_ROOT_COUNT
                and progress.get('http_requests') == 4934
                and len(progress.get('attempted_pids', [])) == 2435,
                'historical mode requires the exact exhausted 870-root progress checkpoint')
        batches = progress.get('batches')
        require(isinstance(batches, list) and len(batches) == 14,
                'historical 870 checkpoint batch history differs from its pinned extent')
        final_batch = batches[-1]
        growth_path = EXPECTED_PROGRESS_DIR / 'batch-0012/growth/accepted-graphs.json'
        receipt = final_batch.get('growth_receipt')
        require(Path(final_batch.get('growth_output', '')).resolve() == growth_path.parent.resolve()
                and isinstance(receipt, dict)
                and receipt.get('path') == str(growth_path)
                and receipt.get('sha256') == HISTORICAL_870_GRAPH_SHA256
                and sha(growth_path) == HISTORICAL_870_GRAPH_SHA256,
                'historical 870 checkpoint does not bind its exact accepted graph')
        require(sha(V16_TERMINAL) == HISTORICAL_870_TERMINAL_SHA256,
                'historical 870 checkpoint terminal differs from its pinned exhausted receipt')
        return progress
    require(type(count) is int and count >= 1000,
            'final checkpoint is below 1000 total roots; full replay is deferred')
    return progress


def verify_runtime():
    require(sha(RUNTIME_MANIFEST) == RUNTIME_MANIFEST_SHA256,
            'frozen runtime manifest hash differs from its approved pin')
    manifest = load(RUNTIME_MANIFEST)
    require(manifest.get('producer') == RUNTIME_PRODUCER
            and manifest.get('code_root') == str(RUNTIME), 'unexpected frozen runtime identity')
    modules = manifest.get('modules')
    require(isinstance(modules, list) and len(modules) == 184,
            'frozen runtime manifest does not enumerate the approved 184 modules')
    seen = set()
    for row in modules:
        require(isinstance(row, dict) and set(row) == {'path', 'sha256'},
                'frozen runtime module receipt is malformed')
        relative = Path(row['path'])
        require(not relative.is_absolute() and '..' not in relative.parts,
                'frozen runtime module path escapes its root')
        path = RUNTIME / relative
        require(path.is_file() and not path.is_symlink() and sha(path) == row['sha256'],
                'a frozen runtime module changed: ' + str(path))
        require(relative.as_posix() not in seen, 'runtime manifest repeats a module')
        seen.add(relative.as_posix())
    require(manifest.get('trusted_data_root') == str(ROOT),
            'frozen runtime names a different trusted data root')
    return manifest


def verify_contracts(manifest):
    install_frozen_imports()
    # A test harness may have imported public-worktree modules before calling
    # this offline contract audit. Ensure the imported campaign functions below
    # originate from the pinned frozen tree, never an existing sys.modules entry.
    for row in manifest['modules']:
        path = Path(row['path'])
        if path.suffix == '.py':
            sys.modules.pop(path.stem, None)
    import expand_genuine_roots as trusted_expand
    import merge_rebased_radio_growth as merge_growth
    import genuine_root_recovery as recovery
    from plan_genius_update import plan_genius_update
    from rebind_native_baseline_alias_chain import verify_native_alias_child_report
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope, radio_snapshot_proof_validation_pass
    from seed_video_evidence import seed_video_selector_cache_scope

    modules = {row['path']: row['sha256'] for row in manifest['modules']}
    loaded_modules = (trusted_expand, merge_growth, recovery, plan_genius_update.__module__,
                      verify_native_alias_child_report.__module__,
                      radio_snapshot_proof_cache_scope.__module__,
                      radio_snapshot_proof_validation_pass.__module__,
                      seed_video_selector_cache_scope.__module__)
    for module in loaded_modules:
        module = module if not isinstance(module, str) else sys.modules[module]
        path = Path(module.__file__).resolve()
        relative = path.relative_to(RUNTIME).as_posix()
        require(modules.get(relative) == sha(path),
                'contract module is not the exact frozen manifest file: ' + relative)

    expected_parameters = {
        trusted_expand._validate_prior_attempt_ledger: ('path', 'library_hash', 'track_snapshot_sha256'),
        trusted_expand._validate_growth_receipts: ('growth_dir',),
        trusted_expand._verify_progress_receipts: ('progress', 'library_hash', 'identity_hash'),
        merge_growth._verify_rebase_receipt: ('graph_path', 'report_path', 'state_path', 'library_hash', 'identity_hash'),
        merge_growth._replay_previous_growth: ('previous_dir', 'baseline_state', 'baseline_roots',
                                               'baseline_provenance', 'matcher', 'library_hash', 'identity_hash'),
        recovery.replay_growth_checkpoint: ('previous_growth', 'track_snapshot', 'identity_map_path',
                                            'baseline_state_path', 'baseline_graph_export_path', 'rebase_report_path'),
    }
    for function, expected in expected_parameters.items():
        actual = tuple(inspect.signature(function).parameters)
        require(actual[:len(expected)] == expected,
                'frozen verifier call signature changed: ' + function.__module__ + '.' + function.__name__)
    def returned_dict_keys(function):
        tree = ast.parse(Path(function.__code__.co_filename).read_text(encoding='utf-8'))
        node = next(child for child in tree.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == function.__name__)
        keys = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                keys.update(key.value for key in child.value.keys
                            if isinstance(key, ast.Constant) and isinstance(key.value, str))
        return keys

    ledger_return = {'attempted_pids', 'same_map_attempted_pids', 'http_requests',
                     'same_map_attempted_pids_by_identity_map',
                     'diagnostic_attempted_pids', 'diagnostic_http_requests', 'outgoing_roots'}
    require(returned_dict_keys(trusted_expand._validate_prior_attempt_ledger) == ledger_return,
            'frozen prior-attempt ledger return fields changed')
    replay_ast = ast.parse(Path(recovery.__file__).read_text(encoding='utf-8'))
    replay_node = next(node for node in replay_ast.body
                       if isinstance(node, ast.FunctionDef) and node.name == 'replay_growth_checkpoint')
    observed_replay_keys = set()
    for node in ast.walk(replay_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            observed_replay_keys.update(key.value for key in node.value.keys
                                        if isinstance(key, ast.Constant) and isinstance(key.value, str))
    require({'state_sha256', 'roots', 'accepted_graphs_sha256', 'outgoing_root_count'} <= observed_replay_keys,
            'frozen checkpoint replay return contract changed')
    dispatcher_source = Path(merge_growth.__file__).read_text(encoding='utf-8')
    dispatcher_constants = merge_growth._replay_previous_growth.__code__.co_consts
    require('opengenius-radio-native-alias-projection-checkpoint-v1' in dispatcher_constants,
            'frozen growth dispatcher lacks NativeAliasProjection replay support')
    require('opengenius-radio-typed-source-union-run-v1' in dispatcher_source
            and 'project_radio_source_union_v05.py' in dispatcher_source,
            'frozen growth dispatcher lacks the pinned typed source-union branch')
    require('opengenius-radio-verified-baseline-policy-inheritance-v1' in dispatcher_source,
            'frozen growth dispatcher lacks the exact typed baseline radio-policy inheritance branch')
    require('radio-only-search-album-fusion-v1'
            in recovery.runner_config_sha256.__code__.co_consts,
            'frozen recovery validator lacks fused collection mode support')
    require(CONTRACT_GROWTH.is_dir(), 'known 732-root growth contract fixture is missing')
    graph, evaluation, state, merge = map(load, (
        CONTRACT_GROWTH / 'accepted-graphs.json', CONTRACT_GROWTH / 'accepted-evaluation.json',
        CONTRACT_GROWTH / 'state.json', CONTRACT_GROWTH / 'merge-audit.json'))
    roots = graph.get('accepted_roots')
    require(graph.get('producer') == 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            and isinstance(roots, list) and evaluation.get('relation_rows') == roots,
            'known normal-growth checkpoint graph/evaluation export contract changed')
    require(state.get('state_sha256') == evaluation.get('virtual_state_sha256')
            and state.get('state_sha256') == merge.get('next_state_sha256')
            and merge.get('output_active_outgoing_root_count') == 732,
            'known normal-growth checkpoint state/merge export contract changed')
    require(sum(bool(row.get('ordered_target_pids')) for row in roots) == 732,
            'known growth format fixture no longer has the expected 732 roots')
    native_receipt = load(EXPECTED_NATIVE / 'native-baseline-alias-child-receipt.json')
    historical_manifest_path = HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json'
    require(sha(historical_manifest_path) == HISTORICAL_NATIVE_MANIFEST_SHA256,
            'historical Native v06 runtime manifest differs from its fixed pin')
    historical_manifest = load(historical_manifest_path)
    require(historical_manifest.get('producer') == 'opengenius-final-campaign-runtime-v06'
            and historical_manifest.get('code_root') == str(HISTORICAL_NATIVE_RUNTIME)
            and historical_manifest.get('trusted_data_root') == str(ROOT),
            'historical Native v06 runtime identity differs')
    historical_modules = historical_manifest.get('modules')
    require(isinstance(historical_modules, list) and len(historical_modules) == 171,
            'historical Native v06 manifest does not enumerate the approved module closure')
    historical_hashes = {}
    for row in historical_modules:
        require(isinstance(row, dict) and set(row) == {'path', 'sha256'},
                'historical Native v06 module receipt is malformed')
        relative = Path(row['path'])
        require(not relative.is_absolute() and '..' not in relative.parts
                and relative.as_posix() not in historical_hashes,
                'historical Native v06 module path is unsafe or duplicated')
        path = HISTORICAL_NATIVE_RUNTIME / relative
        require(path.is_file() and not path.is_symlink() and sha(path) == row['sha256'],
                'historical Native v06 module changed: ' + str(path))
        historical_hashes[relative.as_posix()] = row['sha256']
    expected_native_code_paths = {
        'scripts/rebind_native_baseline_alias_chain.py',
        'scripts/rebind_genius_baseline_identity.py',
        'scripts/rebind_radio_growth_alias_chain.py',
        'scripts/rebind_radio_growth_identity.py',
        'scripts/rebind_radio_growth_album_append_chain.py',
        'scripts/merge_rebased_radio_growth.py',
        'scripts/plan_genius_update.py',
        'scripts/update_genius_bundle.py',
        'scripts/music_identity_map.py',
    }
    code_inputs = native_receipt.get('code_inputs')
    require(isinstance(code_inputs, list) and len(code_inputs) == 9,
            'Native child receipt does not contain its exact nine-code closure')
    seen_native_code = set()
    for code_input in code_inputs:
        require(isinstance(code_input, dict) and set(code_input) == {'path', 'sha256'},
                'Native child code receipt is malformed')
        path = Path(code_input['path']).resolve()
        try:
            relative = path.relative_to(HISTORICAL_NATIVE_RUNTIME).as_posix()
        except ValueError:
            raise ValueError('Native child code receipt escapes its historical v06 root') from None
        require(relative in expected_native_code_paths and relative not in seen_native_code
                and historical_hashes.get(relative) == code_input['sha256'] == sha(path),
                'Native child code receipt differs from its exact historical v06 closure: ' + relative)
        seen_native_code.add(relative)
    require(seen_native_code == expected_native_code_paths,
            'Native child code receipt omits a fixed historical v06 module')
    return {'contract_status': 'passed', 'trusted_api_count': len(expected_parameters),
            'runtime_module_count': len(modules), 'normal_growth_fixture_roots': 732,
            'native_code_receipts': len(code_inputs),
            'historical_native_runtime_manifest_sha256': HISTORICAL_NATIVE_MANIFEST_SHA256,
            'projection_dispatch': 'present', 'typed_source_union_dispatch': 'present',
            'fusion_mode_dispatch': 'present'}


def install_frozen_imports():
    # Keep the campaign producer frozen and prevent Python from writing pyc files there.
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = str(ROOT)
    frozen_scripts = str(RUNTIME / 'scripts')
    public_scripts = str(ROOT / 'scripts')
    sys.path[:] = [p for p in sys.path if str(Path(p or '.').resolve()) != public_scripts]
    sys.path.insert(0, frozen_scripts)


def _config_file(config, name, expected_path=None, expected_sha=None):
    row = config.get(name)
    require(isinstance(row, dict) and set(row) == {'path', 'sha256'},
            'progress config lacks exact ' + name + ' path/hash receipt')
    path = Path(row['path']).resolve()
    require(path.is_relative_to(ROOT.resolve()) or path.is_relative_to(Path('/tmp').resolve()),
            name + ' source path is outside approved data roots')
    require(path.is_file() and sha(path) == row['sha256'], name + ' source receipt changed')
    if expected_path is not None:
        require(path == Path(expected_path).resolve(), name + ' points to an unexpected source path')
    if expected_sha is not None:
        require(row['sha256'] == expected_sha, name + ' hash differs from the reviewed v14 source')
    return path


def _system_python_code_roots():
    """Return only this interpreter's standard site-package code roots."""
    roots = set()
    version = f'{sys.version_info.major}.{sys.version_info.minor}'
    for key in ('purelib', 'platlib'):
        value = sysconfig.get_paths().get(key)
        if value:
            path = Path(value).resolve()
            if path.name in ('site-packages', 'dist-packages'):
                roots.add(path)
    roots.update((
        Path('/usr/lib/python3/dist-packages'),
        Path(f'/usr/lib/python{version}/site-packages'),
        Path(f'/usr/lib/python{version}/dist-packages'),
        Path(f'/usr/local/lib/python{version}/site-packages'),
        Path(f'/usr/local/lib/python{version}/dist-packages'),
        ROOT / '.venv' / 'lib' / f'python{version}' / 'site-packages',
    ))
    return tuple(path.resolve() for path in roots)


def _resolve_frozen_code_copy(original_path, expected_sha256):
    """Resolve historical code only through an exact hash-pinned copy."""
    original_path = Path(original_path).resolve()
    if (HISTORICAL_CODE_ARCHIVE_MANIFEST.is_file()
            and sha(HISTORICAL_CODE_ARCHIVE_MANIFEST) == HISTORICAL_CODE_ARCHIVE_MANIFEST_SHA256):
        archive = load(HISTORICAL_CODE_ARCHIVE_MANIFEST)
        if (archive.get('producer') == 'opengenius-historical-code-receipt-byte-match-archive-v1'
                and archive.get('schema_version') == 1
                and archive.get('code_is_not_executed') is True):
            for row in archive.get('entries', []):
                if (row.get('original_expected_path') != str(original_path)
                        or row.get('expected_sha256') != expected_sha256
                        or row.get('actual_resolved_source_sha256') != expected_sha256
                        or row.get('archive_sha256') != expected_sha256):
                    continue
                candidate = Path(row.get('archive_path', '')).resolve()
                try:
                    candidate.relative_to(HISTORICAL_CODE_ARCHIVE.resolve())
                except ValueError:
                    continue
                if (candidate.is_file() and not candidate.is_symlink()
                        and sha(candidate) == expected_sha256):
                    return candidate
    try:
        relative = original_path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    if relative.suffix != '.py' or not relative.parts or relative.parts[0] not in ('scripts', 'tests'):
        return None
    manifests = (
        (V14_RUNTIME_MANIFEST, V14_RUNTIME_MANIFEST_SHA256, V14_RUNTIME),
        (RUNTIME_MANIFEST, RUNTIME_MANIFEST_SHA256, RUNTIME),
        (HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json',
         HISTORICAL_NATIVE_MANIFEST_SHA256, HISTORICAL_NATIVE_RUNTIME),
    )
    for manifest_path, manifest_sha256, runtime_root in manifests:
        if not manifest_path.is_file() or sha(manifest_path) != manifest_sha256:
            continue
        manifest = load(manifest_path)
        matching = any(row.get('path') == relative.as_posix()
                       and row.get('sha256') == expected_sha256
                       for row in manifest.get('modules', []) if isinstance(row, dict))
        if not matching:
            continue
        candidate = runtime_root / relative
        if candidate.is_file() and not candidate.is_symlink() and sha(candidate) == expected_sha256:
            return candidate.resolve()
    return None


def _receipt_closure(roots):
    """Collect and hash-check file receipts reachable from validated JSON roots."""
    receipts = {}
    visited_json = set()
    queue = [Path(path).resolve() for path in roots]
    hash_cache = {}

    def closure_sha(path):
        path = Path(path).resolve()
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = hash_cache.get(path)
        if cached is None or cached[0] != identity:
            cached = (identity, sha(path))
            hash_cache[path] = cached
        return cached[1]

    def approved_manifest_code_root(manifest_path, document):
        manifest_path = Path(manifest_path).resolve()
        approved = (
            (RUNTIME / 'runtime-manifest.json', RUNTIME_MANIFEST_SHA256,
             RUNTIME_PRODUCER, RUNTIME),
            (V14_RUNTIME_MANIFEST, V14_RUNTIME_MANIFEST_SHA256,
             'opengenius-v11-source-union-v07-v14-ledger-runtime-v1',
             V14_RUNTIME),
            (ACCEPTED_SUFFIX_RUNTIME / 'runtime-manifest.json',
             '29e1336c39be927840fee071a1d40dfb862d4819ed49df1d473a92b013b44ebe',
             'opengenius-final-campaign-runtime-v11-source-union-v09-suffix-orphan',
             ACCEPTED_SUFFIX_RUNTIME),
            (ACCEPTED_TITLE_RUNTIME / 'runtime-manifest.json',
             '5623c0e3bf6390cb401557d327b1e6b511629fbdcea5315d00b0bb53467c1c39',
             'opengenius-final-campaign-runtime-v11-source-union-v10-title-origin',
             ACCEPTED_TITLE_RUNTIME),
            (ACCEPTED_LEDGER_RUNTIME / 'runtime-manifest.json',
             'c93e6086ff4c37eced4433cb170991c44a720498e3618b785c2f250568ce2072',
             'opengenius-final-campaign-runtime-v11-source-union-v11-title-ledger',
             ACCEPTED_LEDGER_RUNTIME),
        )
        for fixed_path, fixed_sha, fixed_producer, fixed_root in approved:
            if (manifest_path == fixed_path.resolve() and sha(manifest_path) == fixed_sha
                    and document.get('producer') == fixed_producer
                    and document.get('code_root') == str(fixed_root.resolve())):
                return fixed_root.resolve()
        historical_path = HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json'
        if (manifest_path == historical_path.resolve()
                and sha(manifest_path) == HISTORICAL_NATIVE_MANIFEST_SHA256
                and document.get('producer') == 'opengenius-final-campaign-runtime-v06'
                and document.get('code_root') == str(HISTORICAL_NATIVE_RUNTIME.resolve())):
            return HISTORICAL_NATIVE_RUNTIME.resolve()
        return None

    while queue:
        path = queue.pop()
        if path in visited_json or not path.is_file():
            continue
        visited_json.add(path)
        try:
            document = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        def walk(value):
            if isinstance(value, dict):
                if (set(value) == {'path', 'sha256'} and isinstance(value['path'], str)
                        and isinstance(value['sha256'], str)):
                    raw_source = Path(value['path'])
                    if not raw_source.is_absolute():
                        require('..' not in raw_source.parts,
                                'relative source receipt contains parent traversal')
                        receipt_base = approved_manifest_code_root(path, document) or ROOT.resolve()
                        raw_source = receipt_base / raw_source
                    source = raw_source.resolve()
                    allowed_roots = (ROOT.resolve(), Path('/tmp').resolve(),
                                     Path('/mnt/data/mizoyuzu/Music.app').resolve(),
                                     *_system_python_code_roots())
                    system_code_roots = _system_python_code_roots()
                    under_system_packages = any(
                        source == allowed or source.is_relative_to(allowed)
                        for allowed in system_code_roots)
                    require((any(source == allowed or source.is_relative_to(allowed)
                                 for allowed in allowed_roots)
                             and (not under_system_packages or source.suffix == '.py')),
                            'source receipt resolves outside approved data roots: ' + str(source))
                    if source.is_dir():
                        source = source / 'accepted-graphs.json'
                    if not source.is_file() or closure_sha(source) != value['sha256']:
                        frozen_copy = _resolve_frozen_code_copy(source, value['sha256'])
                        require(frozen_copy is not None,
                                'source closure receipt changed: ' + str(source))
                        receipts['receipt-original:' + str(source)] = value['sha256']
                        source = frozen_copy
                    require(source.is_file() and closure_sha(source) == value['sha256'],
                            'resolved source closure receipt changed: ' + str(source))
                    receipts[str(source)] = value['sha256']
                    if source.suffix.lower() == '.json':
                        queue.append(source)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(document)
    receipts.update({str(path): closure_sha(path) for path in sorted(visited_json)})
    return receipts


def _replay_trusted_sources(*, trusted_expand, merge_growth,
                            identity_map, plan_genius_update,
                            load_library, progress, snapshot_path,
                            map_path, ledger_path, projection_dir,
                            baseline_state_path, baseline_graph_path,
                            rebase_report_path):
    # Use the same shared invocation scopes as the approved runner. Without
    # these scopes, ledger validation, progress validation, and typed replay
    # would independently repeat the expensive radio-source proof closure.
    from radio_snapshot_proof import (radio_snapshot_proof_cache_scope,
                                     radio_snapshot_proof_validation_pass)
    from seed_video_evidence import seed_video_selector_cache_scope

    with radio_snapshot_proof_cache_scope(), seed_video_selector_cache_scope(), \
            radio_snapshot_proof_validation_pass():
        tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
        require(len(tracks) == 3004 and library_hash == EXPECTED['library'],
                'snapshot does not describe the pinned 3004-track Library')
        identity_hash = sha(map_path)
        prior = trusted_expand._validate_prior_attempt_ledger(
            ledger_path, library_hash, sha(snapshot_path), authorized_rebound_retry_pids=set(),
            current_identity_map_sha256=identity_hash)
        require(prior['outgoing_roots'] == 782,
                'title-child attempt ledger does not resolve to the accepted 782-root baseline')
        if projection_dir is not None:
            trusted_expand._validate_growth_receipts(projection_dir)
        require(progress.get('batches'), 'final campaign has no fused collection batches')
        for batch in progress['batches']:
            require(batch.get('source_policy') == progress['config']['source_policy'],
                    'a final campaign batch has the wrong source policy')
        verified_progress = copy.deepcopy(progress)
        release_pids = set(progress['config'].get('authorized_retry_release_pids', []))
        require(not release_pids, 'title-map campaign must not release prior same-map attempts')
        trusted_expand._verify_progress_receipts(
            verified_progress, library_hash, identity_hash,
            prior['attempted_pids'] - release_pids, prior['http_requests'])
        require(verified_progress['attempted_pids'] == progress['attempted_pids'],
                'progress attempted PIDs differ after trusted receipt replay')
        final_batch = progress['batches'][-1]
        final_dir = Path(final_batch.get('growth_output', '')).resolve()
        growth_receipt = final_batch.get('growth_receipt')
        require(final_dir.is_dir() and isinstance(growth_receipt, dict)
                and set(growth_receipt) == {'path', 'sha256'},
                'final progress does not identify its last accepted growth checkpoint')
        require(Path(growth_receipt['path']).resolve() == final_dir / 'accepted-graphs.json'
                and sha(final_dir / 'accepted-graphs.json') == growth_receipt['sha256'],
                'last progress graph receipt does not bind the final checkpoint')
        matcher = identity_map(json.loads(map_path.read_bytes()), tracks, library_hash)
        baseline_graph, _, baseline_state, provenance = merge_growth._verify_rebase_receipt(
            baseline_graph_path, rebase_report_path, baseline_state_path,
            library_hash, identity_hash)
        rebuilt_baseline = plan_genius_update(
            baseline_state, baseline_graph['accepted_roots'], matcher,
            library_hash, identity_hash)['next_state']
        require(rebuilt_baseline['state_sha256'] == baseline_state['state_sha256'],
                'trusted Native baseline does not reproduce its typed state')
        state, roots, normalized_runs, accepted_hash = merge_growth._replay_previous_growth(
            final_dir, baseline_state, baseline_graph['accepted_roots'], provenance,
            matcher, library_hash, identity_hash)
        replay = {'state_sha256': state['state_sha256'], 'roots': roots,
                  'accepted_graphs_sha256': accepted_hash,
                  'outgoing_root_count': sum(bool(row['ordered_target_pids']) for row in roots),
                  'state_document': state, 'normalized_runs': normalized_runs}
    return tracks, library_hash, identity_hash, prior, final_dir, growth_receipt, replay


def _verify_v16_recovery_continuation(progress_path, progress, historical_exhausted_870=False):
    """Bind the v16 recovery to its failed v15 predecessor and exact pre-API seal."""
    required = (V15_RECOVERY_RUNNER, V15_INVOCATION, V15_PROCESS, V15_TERMINAL,
                V16_RECOVERY_RUNNER, V16_INVOCATION, V16_PROCESS, V16_TERMINAL,
                V16_RECOVERED_CHECKPOINT, V16_PRESERVATION,
                V13_FAILED_TERMINAL, V13_FAILED_CHECKPOINT,
                EXPECTED_PROGRESS_DIR / 'v13-continuation-invocation-v05.json', RUNTIME_MANIFEST)
    require(all(Path(path).is_file() for path in required),
            'v16 recovery/continuation lineage is incomplete')
    require(sha(V15_RECOVERY_RUNNER) == V15_RECOVERY_RUNNER_SHA256
            and sha(V16_RECOVERY_RUNNER) == V16_RECOVERY_RUNNER_SHA256
            and sha(RUNTIME_MANIFEST) == RUNTIME_MANIFEST_SHA256,
            'v15/v16 runner or runtime manifest changed')
    failed_v13 = load(V13_FAILED_TERMINAL)
    v13_checkpoint = load(V13_FAILED_CHECKPOINT)
    require(failed_v13.get('status') == 'failed' and failed_v13.get('returncode') == 1
            and failed_v13.get('progress_sha256') == sha(V13_FAILED_CHECKPOINT)
            and sha(V13_FAILED_CHECKPOINT) ==
            '4bbd6e564c289ee54618ce97432e51fdc7bc043960e7fe120e30c66d6dafa356'
            and v13_checkpoint.get('successful_outgoing_roots') == 826,
            'v13 failed, uncredited checkpoint is not preserved')
    v15_inv, v15_proc, v15_term = map(load, (V15_INVOCATION, V15_PROCESS, V15_TERMINAL))
    require(v15_term.get('status') == 'failed' and v15_term.get('returncode') == 1
            and v15_term.get('progress_sha256') == sha(V13_FAILED_CHECKPOINT)
            and v15_term.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v15_term.get('invocation_sha256') == sha(V15_INVOCATION)
            and v15_proc.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v15_proc.get('script_sha256') == V15_RECOVERY_RUNNER_SHA256
            and v15_inv.get('script_sha256') == V15_RECOVERY_RUNNER_SHA256
            and v15_inv.get('progress_before_sha256') == sha(V13_FAILED_CHECKPOINT)
            and set(v15_proc) == set(v15_inv) | {'started_unix', 'worker_pid'}
            and all(v15_proc.get(key) == value for key, value in v15_inv.items()),
            'stopped v15 run is not preserved as failed, zero-credit history')
    v16_inv, v16_proc, v16_term = map(load, (V16_INVOCATION, V16_PROCESS, V16_TERMINAL))
    recovered = load(V16_RECOVERED_CHECKPOINT)
    preservation = load(V16_PRESERVATION)
    require(v16_inv.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v16_inv.get('script_sha256') == V16_RECOVERY_RUNNER_SHA256
            and v16_inv.get('source_v05_invocation_sha256') == sha(
                EXPECTED_PROGRESS_DIR / 'v13-continuation-invocation-v05.json')
            and v16_inv.get('progress_before_sha256') == sha(V13_FAILED_CHECKPOINT),
            'v16 invocation does not descend from the unchanged 826-root checkpoint')
    require(v16_proc.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v16_proc.get('script_sha256') == V16_RECOVERY_RUNNER_SHA256
            and set(v16_proc) == set(v16_inv) | {'started_unix', 'worker_pid'}
            and all(v16_proc.get(key) == value for key, value in v16_inv.items())
            and type(v16_proc.get('worker_pid')) is int and v16_proc['worker_pid'] > 0,
            'v16 process receipt does not bind the exact invocation')
    expected_progress_status = ('exhausted_candidates' if historical_exhausted_870
                                else 'target_reached')
    require(v16_term.get('status') == 'completed' and v16_term.get('returncode') == 0
            and v16_term.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v16_term.get('invocation_sha256') == sha(V16_INVOCATION)
            and v16_term.get('pretransport_gate_passed') is True
            and v16_term.get('progress_sha256') == sha(progress_path)
            and v16_term.get('progress_status') == expected_progress_status
            and v16_term.get('roots') == progress.get('successful_outgoing_roots')
            and v16_term.get('http_requests_total') == progress.get('http_requests')
            and v16_term.get('attempted_pids') == len(progress.get('attempted_pids', [])),
            'v16 terminal does not bind the completed target progress')
    require(recovered.get('successful_outgoing_roots') == 840
            and recovered.get('http_requests') == 4225
            and len(recovered.get('attempted_pids', [])) == 1925
            and preservation.get('status') ==
            'passed_recovered_checkpoint_preservation_before_new_transport'
            and preservation.get('progress_sha256') == sha(V16_RECOVERED_CHECKPOINT)
            and preservation.get('roots') == 840
            and preservation.get('native_roots') == 159
            and preservation.get('yt_roots') == 681
            and preservation.get('edges') == 19527
            and preservation.get('http_total') == 4225 and preservation.get('attempts') == 1925
            and preservation.get('parent_graph_rows_preserved') == 987
            and preservation.get('new_authenticated_radio_roots') == 14
            and preservation.get('new_radio_edges') == 535
            and preservation.get('parent_assignments_preserved') == 1021
            and preservation.get('parent_rows_unchanged') == 1008
            and preservation.get('incoming_only_rows_promoted') == 13
            and preservation.get('parent_identity_and_metadata_preserved') is True
            and preservation.get('all_state_relations_match_accepted_pid_vectors') is True
            and preservation.get('native_assignments_preserved') == 515,
            'v16 pretransport checkpoint promotion/preservation receipt changed')
    argv = v16_inv.get('argv')
    require(isinstance(argv, list) and len(argv) > 2 and all(isinstance(x, str) for x in argv)
            and argv[0] == str(ROOT / '.venv/bin/python')
            and Path(argv[1]).resolve() == (RUNTIME / 'scripts/expand_genuine_roots.py').resolve(),
            'v16 invocation does not use the manifest-pinned campaign entrypoint')
    args = argv[2:]
    parsed = {}
    index = 0
    while index < len(args):
        require(args[index].startswith('--') and index + 1 < len(args)
                and args[index] not in parsed, 'v16 invocation options are malformed')
        parsed[args[index]] = args[index + 1]
        index += 2
    expected = {'--target-outgoing-roots': '1000', '--global-request-budget': '6500',
                '--max-attempts': '2800', '--batch-size': '44',
                '--batch-request-budget': '200', '--interval': '3',
                '--candidate-limit': '100', '--stop-after-batches': '20',
                '--source-policy': 'authenticated-watch-radio-only-v1',
                '--collector-collection-mode': 'radio-only-v1',
                '--output': str(Path(progress_path).resolve().parent)}
    require(all(parsed.get(key) == value for key, value in expected.items()),
            'v16 continuation changed its reviewed caps, policy, pacing, or output')
    config = progress.get('config', {})
    require(config.get('target_outgoing_roots') == 1000
            and config.get('global_request_budget') == 6500
            and config.get('max_attempts') == 2800
            and config.get('batch_size') == 44
            and config.get('batch_request_budget') == 200
            and config.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and config.get('collector_collection_mode') == 'radio-only-v1'
            and config.get('authorized_retry_release_pids') == [],
            'final progress changed the reviewed v16 collection policy')
    return {'v15_failed_terminal_sha256': sha(V15_TERMINAL),
            'v15_invocation_sha256': sha(V15_INVOCATION),
            'v16_runner_sha256': sha(V16_RECOVERY_RUNNER),
            'v16_invocation_sha256': sha(V16_INVOCATION),
            'v16_process_sha256': sha(V16_PROCESS),
            'v16_terminal_sha256': sha(V16_TERMINAL),
            'recovered_checkpoint_sha256': sha(V16_RECOVERED_CHECKPOINT),
            'preservation_sha256': sha(V16_PRESERVATION),
            'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256,
            'worker_pid': v16_proc['worker_pid']}


def audit(progress_path, audit_output, goal_metric='total-genuine',
          historical_exhausted_870=False):
    require(goal_metric in ('ytmusic-backed', 'total-genuine'),
            'unsupported root-count goal metric')
    progress = _precheck_target(progress_path, goal_metric, historical_exhausted_870)
    if historical_exhausted_870:
        require(sha(PREPATCH_AUDITOR_ARCHIVE) == PREPATCH_AUDITOR_ARCHIVE_SHA256,
                'preserved prepatch auditor source differs from its archived receipt')
    inventory, inventory_receipts, native_origin_pids, known_yt_origin_pids = (
        _source_origin_inventory())
    pilot = _verify_historical_pilot()
    continuation = _verify_v14_continuation_lineage()
    v16_execution = _verify_v16_recovery_continuation(
        progress_path, progress, historical_exhausted_870)
    accepted_parent_yt = _accepted_782_yt_origin_pids()
    require(native_origin_pids.isdisjoint(accepted_parent_yt)
            and len(accepted_parent_yt) == 623,
            'accepted 782-root parent does not preserve the verified 159 Native + 623 YT partition')
    known_yt_origin_pids |= accepted_parent_yt
    manifest = verify_runtime()
    verify_contracts(manifest)
    install_frozen_imports()
    import expand_genuine_roots as trusted_expand
    import merge_rebased_radio_growth as merge_growth
    from music_identity_map import IdentityMap, load_library
    from plan_genius_update import plan_genius_update
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope
    from radio_snapshot_proof import radio_snapshot_proof_validation_pass
    from seed_video_evidence import seed_video_selector_cache_scope

    # Assert key trusted imports came from the frozen runtime, and not a
    # same-named module already visible from the public worktree.
    runtime_modules = {row['path']: row['sha256'] for row in manifest['modules']}
    for module in (trusted_expand, merge_growth, IdentityMap.__module__, load_library.__module__,
                   plan_genius_update.__module__,
                   radio_snapshot_proof_cache_scope.__module__, radio_snapshot_proof_validation_pass.__module__,
                   seed_video_selector_cache_scope.__module__):
        loaded = module if not isinstance(module, str) else sys.modules[module]
        source_path = Path(loaded.__file__).resolve()
        try:
            relative = source_path.relative_to(RUNTIME).as_posix()
        except ValueError:
            raise ValueError('trusted campaign module loaded outside the frozen runtime: ' + str(source_path))
        require(runtime_modules.get(relative) == sha(source_path),
                'loaded trusted module differs from its frozen manifest receipt: ' + relative)

    progress_path = Path(progress_path).resolve()
    audit_output = Path(audit_output).resolve()
    receipt_output = audit_output.with_name(audit_output.stem + '.receipt.json')
    require(not audit_output.exists() and not receipt_output.exists(),
            'source-audit outputs must be new; preserve prior receipts')
    protected = (EXPECTED_PROGRESS_DIR.resolve(), EXPECTED_PROJECTION.resolve(),
                 EXPECTED_NATIVE.resolve(), RUNTIME.resolve(),
                 (ROOT / 'data/new-library-rebased-20261009-v03').resolve(),
                 SOURCE_ORIGIN_INVENTORY.resolve())
    for source_root in protected:
        require(not (audit_output == source_root or audit_output.is_relative_to(source_root)
                     or source_root.is_relative_to(audit_output)),
                'source-audit output overlaps a frozen campaign or baseline input')
    inventory_doc = load(SOURCE_ORIGIN_INVENTORY)
    for source_path in inventory_doc.get('source_artifact_sha256', {}):
        source_path = Path(source_path).resolve()
        require(audit_output != source_path,
                'source-audit output would overwrite a historical source receipt')
    require(progress_path.parent == EXPECTED_PROGRESS_DIR.resolve(),
            'progress path is not in the designated v15 title-map acquisition output')
    config = progress.get('config')
    require(isinstance(config, dict) and progress.get('schema_version') == 1,
            'final progress header is malformed')
    if historical_exhausted_870:
        require(progress.get('status') == 'exhausted_candidates',
                'historical source audit requires the exhausted-candidates terminal state')
    else:
        _require_final_progress_status(progress.get('status'))
    require(config.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and config.get('collector_collection_mode') == 'radio-only-v1',
            'final progress is not bound to the approved authenticated radio-only source mode')
    require(config.get('dynamic_current_radio_hints') is False
            and config.get('external_seed_video_evidence') == {
                'path': str(V15_SEED_EVIDENCE), 'sha256': V15_SEED_EVIDENCE_SHA256},
            'v15 requires its exact reviewed current-map standard seed-video evidence receipt')

    snapshot_path = _config_file(config, 'track_snapshot', EXPECTED_SNAPSHOT, EXPECTED['snapshot'])
    map_path = _config_file(config, 'identity_map', EXPECTED_MAP, EXPECTED['map'])
    baseline_state_path = _config_file(
        config, 'baseline_state', EXPECTED_BASELINE_CHILD / 'state.json', EXPECTED['native_state_file'])
    baseline_graph_path = _config_file(
        config, 'baseline_graph_export', EXPECTED_BASELINE_CHILD / 'accepted-baseline-graphs.json', EXPECTED['native_graph'])
    rebase_report_path = _config_file(
        config, 'rebase_report', EXPECTED_BASELINE_CHILD / 'identity-map-rebinding-report.json', EXPECTED['native_report'])
    ledger_path = _config_file(config, 'prior_attempt_ledger')
    previous = config.get('previous_growth')
    projection_dir = None
    projection_graph = None
    if previous is not None:
        require(isinstance(previous, dict) and set(previous) == {'path', 'sha256'},
                'final progress previous-growth receipt is malformed')
        projection_dir = Path(previous['path']).resolve()
        require(projection_dir == EXPECTED_SOURCE_UNION.resolve(),
                'final progress does not use the exact accepted v06 source-union parent')
        projection_graph = projection_dir / 'accepted-graphs.json'
        require(projection_graph.is_file() and sha(projection_graph) == previous['sha256'],
                'typed previous-growth parent graph receipt changed')
    require(sha(EXPECTED_NATIVE / 'native-baseline-alias-child-receipt.json') == EXPECTED['native_receipt'],
            'native alias child receipt changed')

    tracks, library_hash, identity_hash, prior, final_dir, growth_receipt, replay = (
        _replay_trusted_sources(
            trusted_expand=trusted_expand,
            merge_growth=merge_growth,
            identity_map=IdentityMap, plan_genius_update=plan_genius_update,
            load_library=load_library, progress=progress,
            snapshot_path=snapshot_path,
            map_path=map_path, ledger_path=ledger_path,
            projection_dir=projection_dir, baseline_state_path=baseline_state_path,
            baseline_graph_path=baseline_graph_path,
            rebase_report_path=rebase_report_path))
    graph_path = final_dir / 'accepted-graphs.json'
    evaluation_path = final_dir / 'accepted-evaluation.json'
    state_path = final_dir / 'state.json'
    merge_path = final_dir / 'merge-audit.json'
    graph, evaluation, state, merge = map(load, (graph_path, evaluation_path, state_path, merge_path))
    baseline_policy_transition = _validate_verified_radio_baseline_policy_transition(graph)
    roots = graph.get('accepted_roots')
    require(isinstance(roots, list), 'replayed final graph has no root array')
    root_pids = [row.get('root_pid') for row in roots if row.get('ordered_target_pids')]
    require(len(root_pids) == len(set(root_pids)),
            'final checkpoint has duplicate nonempty outgoing root PIDs')
    source_origin = _classify_final_source_roots(
        set(root_pids), native_origin_pids, known_yt_origin_pids,
        replay['normalized_runs'])
    if historical_exhausted_870:
        qualifying_count = _require_historical_870_partition(source_origin, goal_metric)
    else:
        qualifying_count = _require_goal(source_origin, goal_metric)
    require(replay['outgoing_root_count'] == len(root_pids)
            == progress.get('successful_outgoing_roots'),
            'progress, accepted graph, and independent whole-chain replay root totals differ')
    require(replay['accepted_graphs_sha256'] == sha(graph_path),
            'trusted replay does not bind the final accepted graph bytes')
    replay_roots_match = replay['roots'] == roots
    replay_state_python_equal = replay['state_document'] == state
    replay_state_canonical_json_equal = (
        _canonical_document(replay['state_document']) == _canonical_document(state))
    require(replay_roots_match,
            'replayed accepted-root rows differ from the final graph document')
    require(replay_state_canonical_json_equal,
            'replayed final state JSON differs from the materialized state document')
    require(evaluation.get('relation_rows') == roots,
            'final evaluation ordered relation rows differ from accepted graph')
    require(evaluation.get('virtual_state_sha256') == state.get('state_sha256')
            and merge.get('next_state_sha256') == state.get('state_sha256')
            and replay.get('state_sha256') == state.get('state_sha256'),
            'final state seal differs between typed replay, evaluation, and merge audit')
    require(merge.get('output_active_outgoing_root_count') == len(root_pids),
            'merge audit root count differs from replayed graph')

    # The validated receipt graph includes v09 progress, its recursively validated
    # attempt ledger (v09b1-10 and older ledgers), all batch receipts, and the
    # projection/native transition inputs. Requiring every path/hash pair here
    # makes the audit artifact carry the actual input closure, not a claimed scope.
    source_roots = [progress_path, V15_RECOVERY_RUNNER, V15_INVOCATION, V15_PROCESS, V15_TERMINAL,
                    V16_RECOVERY_RUNNER, V16_INVOCATION, V16_PROCESS, V16_TERMINAL,
                    V16_RECOVERED_CHECKPOINT, V16_PRESERVATION,
                    V13_FAILED_TERMINAL, V13_FAILED_CHECKPOINT, ledger_path, graph_path, evaluation_path,
                    state_path, merge_path,
                    EXPECTED_NATIVE / 'native-baseline-alias-child-receipt.json',
                    baseline_state_path, baseline_graph_path, rebase_report_path,
                    snapshot_path, map_path, RUNTIME_MANIFEST,
                    HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json',
                    EXPECTED_V17_AUDITOR,
                    ACCEPTED_PARENT_DIR / 'progress.json',
                    ACCEPTED_PARENT_GROWTH / 'accepted-graphs.json',
                    ACCEPTED_PARENT_GROWTH / 'accepted-evaluation.json',
                    ACCEPTED_PARENT_GROWTH / 'state.json',
                    ACCEPTED_PARENT_GROWTH / 'merge-audit.json',
                    ACCEPTED_PARENT_DIR / 'v09-replay-terminal.json',
                    ACCEPTED_PARENT_DIR / 'accepted-782-preservation-audit-v01.json',
                    ACCEPTED_PARENT_DIR / 'batch-0001/independent-new-radio-proof-check-v01.json',
                    EXPECTED_BASELINE_CHILD / 'accepted-baseline-graphs.json',
                    EXPECTED_BASELINE_CHILD / 'identity-map-rebinding-report.json',
                    EXPECTED_BASELINE_CHILD / 'state.json',
                    EXPECTED_BASELINE_CHILD / 'full-replay-terminal-v01.json',
                    ACCEPTED_SUFFIX_RUNTIME / 'runtime-manifest.json',
                    ACCEPTED_TITLE_RUNTIME / 'runtime-manifest.json',
                    ACCEPTED_LEDGER_RUNTIME / 'runtime-manifest.json',
                    Path(__file__).resolve(), SOURCE_ORIGIN_INVENTORY, PILOT_PROGRESS, PILOT_PROFILE,
                    LINEAGE_PATH, V14_PROFILE, V14_INTENT, V14_INVOCATION_PLAN,
                    V14_RUNTIME_MANIFEST,
                    V14_SEED_PLAN, V14_SEED_EVIDENCE,
                    V14_PLAN_ONLY_SUMMARY, V15_SEED_EVIDENCE]
    if historical_exhausted_870:
        source_roots.extend((PREPATCH_AUDITOR_ARCHIVE,
                             PREPATCH_AUDITOR_ARCHIVE.with_name('prepatch-auditor-source-receipt.json'),
                             HISTORICAL_CODE_ARCHIVE_MANIFEST,
                             HISTORICAL_CODE_ARCHIVE_RECEIPTS))
    if projection_graph is not None:
        source_roots.append(projection_graph)
    closure = _receipt_closure(source_roots)
    closure.update(inventory_receipts)
    runtime_modules = {row['path']: row['sha256'] for row in manifest['modules']}
    historical_scope = historical_exhausted_870
    audit_doc = {
        'schema_version': 1,
        'producer': ('opengenius-independent-historical-exhausted-870-source-audit-v1'
                     if historical_scope else 'opengenius-independent-ytmusic-source-audit-v1'),
        'status': ('passed_historical_exhausted_source_replay'
                   if historical_scope else 'passed'),
        'historical_checkpoint_only': historical_scope,
        'goal_target_reached': not historical_scope,
        'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256,
        'historical_native_runtime_manifest_sha256': HISTORICAL_NATIVE_MANIFEST_SHA256,
        'trusted_runtime_module_count': len(runtime_modules),
        'trusted_runtime_modules_sha256': runtime_modules,
        'independent_full_chain_replay': {
            'checkpoint': str(final_dir),
            'status': ('passed_historical_exhausted_870' if historical_scope else 'passed'),
            'outgoing_root_count': len(root_pids),
            'accepted_root_rows_python_equal': replay_roots_match,
            'state_document_python_equal': replay_state_python_equal,
            'state_document_canonical_json_equal': replay_state_canonical_json_equal,
            'state_sha256': sha(state_path),
            'replayed_state_sha256': replay['state_sha256'],
            'accepted_graphs_sha256': sha(graph_path),
            'progress_growth_receipt_sha256': growth_receipt['sha256'],
            'graph_receipt_matches': True,
        },
        'source_root_origin': {
            'goal_metric': goal_metric,
            'qualifying_root_count': qualifying_count,
            'known_source_inventory_path': str(SOURCE_ORIGIN_INVENTORY),
            'known_source_inventory_sha256': SOURCE_ORIGIN_INVENTORY_SHA256,
            'known_source_inventory_scope': inventory['status'],
            'native_only_pids': source_origin['native_only_pids'],
            'ytmusic_source_backed_pids': source_origin['ytmusic_source_backed_pids'],
            'native_ytmusic_overlap_pids': source_origin['native_ytmusic_overlap_pids'],
            'unknown_pids': source_origin['unknown_pids'],
            'counts': source_origin['counts'],
            'verified_final_growth_run_count': source_origin['verified_final_growth_run_count'],
            'classification_basis': 'historical source PID inventory plus the independently verified accepted 782-root parent (159 original Native + 623 typed YT source origins), unioned with same-PID authenticated snapshot roots from whole-chain replay; final active PID set must be exactly classified',
        },
        'source_policy_audit': {
            'verified_radio_baseline_policy_transition': baseline_policy_transition,
            'prior_attempt_ledger_outgoing_roots': prior['outgoing_roots'],
            'prior_attempt_ledger_http_requests': prior['http_requests'],
            'final_progress_http_requests': progress.get('http_requests'),
            'final_progress_attempted_pid_count': len(progress.get('attempted_pids', [])),
            'final_collector_collection_mode': config['collector_collection_mode'],
            'final_source_policy': config['source_policy'],
            'typed_previous_growth_parent_sha256': previous.get('sha256') if previous else None,
            'typed_previous_growth_parent_path': str(projection_dir) if projection_dir else None,
            'new_campaign_batches_verified': len(progress['batches']),
            'historical_retrieval_pilot': pilot,
            'v14_source_union_continuation_lineage': continuation,
            'v16_recovery_continuation_lineage': v16_execution,
            'v14_external_seed_video_evidence_sha256': V14_SEED_EVIDENCE_SHA256,
            'v14_invocation_plan_sha256': V14_INVOCATION_PLAN_SHA256,
            'v15_standard_seed_video_evidence_sha256': V15_SEED_EVIDENCE_SHA256,
            'accepted_782_parent_yt_source_pid_count': len(accepted_parent_yt),
            'typed_source_union_run_verified': any(
                run.get('producer') == 'opengenius-radio-typed-source-union-run-v1'
                for run in replay['normalized_runs']),
            'v09b1_10_history_verified_by_recursive_ledger': True,
            'historical_source_inventory_receipt_count': len(inventory_receipts),
            'historical_source_inventory_sha256': SOURCE_ORIGIN_INVENTORY_SHA256,
        },
        'final_files_sha256': {
            'accepted_graphs': sha(graph_path),
            'accepted_evaluation': sha(evaluation_path),
            'state': sha(state_path),
            'merge_audit': sha(merge_path),
            'progress': sha(progress_path),
        },
        'source_closure_sha256': closure,
    }
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    audit_bytes = (json.dumps(audit_doc, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    with audit_output.open('xb') as stream:
        stream.write(audit_bytes)
    receipt = {
        'schema_version': 1,
        'producer': ('opengenius-independent-historical-exhausted-870-source-audit-receipt-v1'
                     if historical_scope else 'opengenius-independent-ytmusic-source-audit-receipt-v1'),
        'historical_checkpoint_only': historical_scope,
        'goal_target_reached': not historical_scope,
        'source_audit_path': str(audit_output),
        'source_audit_sha256': hashlib.sha256(audit_bytes).hexdigest(),
        'checkpoint_path': str(final_dir),
        'accepted_graphs_sha256': sha(graph_path),
        'state_file_sha256': sha(state_path),
        'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256,
    }
    with receipt_output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    return {'status': ('passed_historical_exhausted_source_replay'
                       if historical_scope else 'passed'), 'outgoing_root_count': len(root_pids),
            'source_audit': str(audit_output), 'source_audit_sha256': sha(audit_output),
            'receipt': str(receipt_output), 'receipt_sha256': sha(receipt_output),
            'source_closure_file_count': len(closure), 'trusted_runtime_module_count': len(runtime_modules)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--progress', type=Path)
    parser.add_argument('--output-audit', type=Path)
    parser.add_argument('--metric', choices=('ytmusic-backed', 'total-genuine'),
                        default='total-genuine',
                        help='root threshold semantics; defaults to the user-approved Native plus YTMusic total')
    parser.add_argument('--historical-exhausted-870', action='store_true',
                        help='audit only the exact pinned exhausted 870-root historical checkpoint; grants no final-goal status')
    parser.add_argument('--verify-runtime-only', action='store_true')
    parser.add_argument('--verify-contract-only', action='store_true')
    args = parser.parse_args()
    if args.verify_runtime_only or args.verify_contract_only:
        manifest = verify_runtime()
        result = {'status': 'runtime_verified', 'module_count': len(manifest['modules']),
                  'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256}
        if args.verify_contract_only:
            result.update(verify_contracts(manifest))
        print(json.dumps(result, sort_keys=True))
        return
    if args.progress is None:
        parser.error('--progress is required for a full source replay')
    if args.output_audit is None:
        parser.error('--output-audit is required for a full source replay')
    print(json.dumps(audit(args.progress, args.output_audit, args.metric,
                           historical_exhausted_870=args.historical_exhausted_870), sort_keys=True))


if __name__ == '__main__':
    main()
