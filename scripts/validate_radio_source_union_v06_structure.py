"""Offline structural check only; this is never a source-acceptance proof.

It builds the complete v06 union documents, plans real state, runs candidate
comparison, and sends them through the exact normal dispatcher. The one costly
historical C10 projection verifier is replaced with its exact already-sealed
current C10 run0 graph/state documents. All files, reports, source proofs,
collector run1 receipts, planner steps, and dispatch comparisons remain real.
No output from this harness earns source-root credit.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v06-c6-cache-rematch-v01')
V10 = Path('/tmp/opengenius-final-campaign-runtime-v10-native-index-compat-v01')
CURRENT = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261013-v13-radio-search-fusion-live/batch-0001/growth'
NATIVE_CHECKPOINT = Path('/tmp/opengenius-native-alias-projection-v06-v01')
MANIFEST_SHA256 = 'd2e005f3b711b2bc20877b9e5353d00c3af6ff4e2aa67f1bcdaf10ecddc7584f'
CURRENT_GRAPH_SHA256 = '937451668dda15b4ff20bc2e03a504c538e89c3c82cf2e617153a059e90c75fe'
NATIVE_CHECKPOINT_HASHES = {
    'accepted-graphs.json': 'e18133786aa8c719c431d9d2d67c1ffde722bcdcd4e5643c21c3cc871762355c',
    'state.json': 'f375894ba4e385cf7895b4d2dd678c540ceaefe5d5fa1747ded2c0f1a4a96cef',
    'relations.json': '26bcc114f8dfce6c209497bae3c57b5997172a6bc2f3bd6e66391998d75700bc',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(raw)


def load_pinned(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load fixed module: ' + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != Path(path).resolve():
        raise RuntimeError('Fixed module resolved to an unexpected path')
    return module


def verify_runtime():
    manifest_path = RUNTIME / 'runtime-manifest.json'
    manifest_raw = manifest_path.read_bytes()
    if sha(manifest_raw) != MANIFEST_SHA256:
        raise ValueError('v06 runtime manifest hash differs from the structural harness pin')
    manifest = json.loads(manifest_raw)
    if (manifest.get('code_root') != str(RUNTIME)
            or manifest.get('producer') != 'opengenius-final-campaign-runtime-v11-source-union-v06-c6-cache-rematch'
            or len(manifest.get('modules', [])) != 175):
        raise ValueError('v06 runtime identity differs from the structural harness pin')
    for row in manifest['modules']:
        path = (RUNTIME / row['path']).resolve()
        if path != RUNTIME / row['path'] or sha(path.read_bytes()) != row['sha256']:
            raise ValueError('v06 runtime module hash changed: ' + row['path'])
    return manifest


def main():
    manifest = verify_runtime()
    v10_manifest = read(V10 / 'runtime-manifest.json')
    os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = str(Path(v10_manifest['trusted_data_root']).resolve(strict=True))
    v10_scripts, v06_scripts = str(V10 / 'scripts'), str(RUNTIME / 'scripts')
    sys.path = [path for path in sys.path if path not in (v10_scripts, v06_scripts)]
    sys.path.insert(0, v10_scripts)
    sys.path.insert(1, v06_scripts)
    merger = load_pinned('merge_rebased_radio_growth', RUNTIME / 'scripts/merge_rebased_radio_growth.py')
    adapter = load_pinned('_structural_source_union_v06_adapter', RUNTIME / 'scripts/project_radio_source_union_v05.py')
    adapter._ACTIVE_MERGER = merger

    source_path = adapter.SOURCE_CHILD / 'accepted-graphs.json'
    source_report_path = adapter.SOURCE_CHILD / 'identity-map-rebinding-report.json'
    source_state_path = adapter.SOURCE_CHILD / 'state.json'
    current_graph_path = CURRENT / 'accepted-graphs.json'
    if sha(current_graph_path.read_bytes()) != CURRENT_GRAPH_SHA256:
        raise ValueError('Pinned C10 graph changed')
    current_graph = read(current_graph_path)
    native_graph = read(adapter.NATIVE_BASELINE / 'accepted-baseline-graphs.json')
    native_report = read(adapter.NATIVE_BASELINE / 'baseline-rebase-report.json')
    native_state = read(adapter.NATIVE_BASELINE / 'baseline-state.json')
    provenance = current_graph.get('baseline_rebase_provenance')
    if not isinstance(provenance, dict) or provenance.get('state_path') != str((adapter.NATIVE_BASELINE / 'baseline-state.json').resolve()):
        raise ValueError('C10 checkpoint does not carry the pinned native baseline provenance')

    projection_docs = {}
    for name, expected in NATIVE_CHECKPOINT_HASHES.items():
        path = NATIVE_CHECKPOINT / name
        raw = path.read_bytes()
        if sha(raw) != expected:
            raise ValueError('Saved Native projection document hash changed: ' + name)
        projection_docs[name] = json.loads(raw)
    checkpoint_graph = projection_docs['accepted-graphs.json']
    if (checkpoint_graph.get('growth_runs') != [current_graph['growth_runs'][0]]
            or len(checkpoint_graph.get('accepted_roots', [])) != 928
            or projection_docs['state.json'].get('state_sha256') != '57bb04774299692912ba7bf0851566d58b8432cfeab8c9eb2059abff2a1af04b'):
        raise ValueError('Saved Native C10 run0 checkpoint differs from the exact accepted run/state')

    original_projection_verifier = merger._verify_native_alias_projection_run
    def use_sealed_c10_projection(run, *, baseline_state, baseline_roots,
                                  baseline_provenance, matcher, library_hash,
                                  identity_hash):
        if run != current_graph['growth_runs'][0]:
            raise ValueError('Structural replay called with another C10 run0 receipt')
        if baseline_state != native_state or baseline_provenance != provenance:
            raise ValueError('Structural replay used another sealed Native baseline/provenance')
        if baseline_roots != native_graph['accepted_roots']:
            raise ValueError('Structural replay used another Native baseline graph')
        return (projection_docs['state.json'], checkpoint_graph['accepted_roots'], run)
    merger._verify_native_alias_projection_run = use_sealed_c10_projection

    proof = __import__('radio_snapshot_proof')
    selector = __import__('seed_video_evidence')
    identity = __import__('music_identity_map')
    tracks, library_hash, _ = identity.load_library(track_snapshot=adapter.TRACK_SNAPSHOT)
    identity_hash = sha(adapter.IDENTITY_MAP.read_bytes())
    matcher = identity.IdentityMap(read(adapter.IDENTITY_MAP), tracks, library_hash)
    baseline_native_context = (native_graph, native_report, native_state, provenance)

    try:
        with proof.radio_snapshot_proof_cache_scope() as proof_stats, \
                selector.seed_video_selector_cache_scope() as selector_stats, \
                proof.radio_snapshot_proof_validation_pass() as pass_stats:
            # Real verification of the Native baseline; this is not mocked.
            verified_native = merger._verify_rebase_receipt(
                adapter.NATIVE_BASELINE / 'accepted-baseline-graphs.json',
                adapter.NATIVE_BASELINE / 'baseline-rebase-report.json',
                adapter.NATIVE_BASELINE / 'baseline-state.json', library_hash, identity_hash)
            if verified_native != baseline_native_context:
                raise ValueError('Real Native baseline verifier differs from the saved C10 provenance')

            docs = adapter._build_projection_documents(
                native_context=verified_native, merger_override=merger)
            graph = docs['accepted-graphs.json']
            run = graph['growth_runs'][1]
            adapter._validate_saved_union_run_contract(run)
            if (len(graph['accepted_roots']) != 929
                    or sum(bool(row['ordered_target_pids']) for row in graph['accepted_roots']) != 768
                    or sum(len(row['ordered_target_pids']) for row in graph['accepted_roots']) != 18047
                    or len(docs['state.json']['rows']) != 958
                    or run.get('union_nonempty_roots') != 768
                    or run.get('source_rematch_transition', {}).get('new_from_source_parent_roots') != 35):
                raise ValueError('Structural source-union counts or assignment projection failed')
            origin_rows = run['typed_source_origin_graphs']
            if (len(origin_rows) != 609
                    or sum(len(row['ordered_target_pids']) for row in origin_rows) != 14706
                    or run['source_rematch_transition'].get('baseline_nonempty_root_count') != 159):
                raise ValueError('Structural Native/source-origin classifier contract failed')

            with tempfile.TemporaryDirectory(prefix='radio-source-union-v06-structure-') as temp:
                candidate = Path(temp) / 'candidate'
                candidate.mkdir()
                for name, document in docs.items():
                    (candidate / name).write_text(json.dumps(
                        document, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
                static = adapter._verify_projection_candidate(candidate, docs)
                baseline_provenance = verified_native[3]
                dispatched = merger._replay_previous_growth(
                    candidate, verified_native[2], verified_native[0]['accepted_roots'],
                    baseline_provenance, matcher, library_hash, identity_hash)
                final_state, final_roots, final_runs, accepted_hash = dispatched
                if (len(final_runs) != 2 or final_state != docs['state.json']
                        or final_roots != graph['accepted_roots']
                        or accepted_hash != static['output_hashes']['accepted-graphs.json']):
                    raise ValueError('Normal dispatcher structural roundtrip differs from exact producer docs')
                candidate_hashes = static['output_hashes']
        result = {
            'status': 'offline_structural_only_no_source_acceptance',
            'genuine_source_acceptance': False,
            'candidate_deleted_after_check': True,
            'mocked_normative_call': 'C10 run0 only, returning exact pinned v06 accepted graph/state/run docs',
            'real_checks': [
                'all pinned v16 archive proof input byte hashes',
                'real Native baseline typed rebase verifier and current C10 provenance equality',
                'real C10 run1 collector receipt validation',
                'real full state planner and exact 29 empty assignment handling',
                'real typed source union run and 609-origin/159-Native classification',
                'real v06 static candidate comparison',
                'real v06 normal dispatcher recomputation/roundtrip',
            ],
            'output_hashes': candidate_hashes,
            'counts_for_structure_only': {
                'nonempty_roots': 768, 'ordered_edges': 18047,
                'typed_source_origin_roots': 609, 'native_original_roots': 159,
                'source_parent_rematch_roots': 35, 'graph_rows': 929,
                'planner_assignments': 958, 'extra_empty_assignments': 29,
            },
            'proof_cache_stats': dict(proof_stats),
            'selector_cache_stats': dict(selector_stats),
            'validation_pass_stats': {
                'closures_count': len(pass_stats.get('closures', {})),
                'keys_count': len(pass_stats.get('keys', ())),
                'nested': pass_stats.get('nested', 0),
            },
            'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'runtime_manifest_sha256': MANIFEST_SHA256,
        }
        output = Path('/tmp/opengenius-radio-source-union-v06-structural-harness-result.json')
        output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        merger._verify_native_alias_projection_run = original_projection_verifier


if __name__ == '__main__':
    main()
