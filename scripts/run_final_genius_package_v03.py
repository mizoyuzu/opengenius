"""Run the fully gated offline final audit and separate Genius-copy checks.

The only command that writes the physical-package copy is update_genius_bundle,
which is reached only after the supervisor/progress/accepted-graph gate and a
passing un-memoized whole-source audit. All child commands use argv arrays.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v15-shiny-ost-radio-live-v01'
PROGRESS = CAMPAIGN / 'progress.json'
SUPERVISOR_TERMINAL = CAMPAIGN / 'v16-recovery-continuation-terminal-v01.json'
SUPERVISOR_PROCESS = CAMPAIGN / 'v16-recovery-continuation-process-v01.json'
CONTINUATION_INVOCATION = CAMPAIGN / 'v16-recovery-continuation-invocation-v01.json'
CONTINUATION_CHILD = CAMPAIGN / 'v16-recovery-continuation-process-v01.json'
RECOVERY_PROCESS = CAMPAIGN / 'v16-recovery-continuation-process-v01.json'
RECOVERY_TERMINAL = CAMPAIGN / 'v16-recovery-continuation-terminal-v01.json'
RECOVERY_PROGRESS_CHECKPOINT = CAMPAIGN / 'v16-recovered840-progress-checkpoint-v01.json'
V13_FAILED_TERMINAL = CAMPAIGN / 'v13-continuation-supervisor-terminal-v05.json'
V13_FAILED_CHECKPOINT = CAMPAIGN / 'v13-accepted826-progress-checkpoint-v01.json'
V13_FAILED_INVOCATION = CAMPAIGN / 'v13-continuation-invocation-v05.json'
V15_FAILED_TERMINAL = CAMPAIGN / 'v15-recovery-continuation-terminal-v01.json'
V15_FAILED_PROCESS = CAMPAIGN / 'v15-recovery-continuation-process-v01.json'
V15_FAILED_INVOCATION = CAMPAIGN / 'v15-recovery-continuation-invocation-v01.json'
V15_RECOVERY_SCRIPT = Path('/tmp/opengenius-v15-recover-and-continue-v01.py')
V15_RECOVERY_SCRIPT_SHA256 = '46b4d441c63fb4f86709cd11e580389a406537e5e855e945b8ded0e4665763f2'
PRESERVATION_AUDIT = CAMPAIGN / 'v16-recovered840-preservation-before-api-v01.json'
RUNTIME_MANIFEST = Path('/tmp/opengenius-final-campaign-runtime-v15-static-composed-absence-v01/runtime-manifest.json')
RUNTIME_MANIFEST_SHA256 = '83a888856e73f4fb6b51a897bcc96ccfbf17044c2b3d0f24b4e92ae4d0bcb47c'
RUNTIME_ROOT = RUNTIME_MANIFEST.parent
RECOVERY_SCRIPT = Path('/tmp/opengenius-v16-recover-and-continue-v01.py')
RECOVERY_SCRIPT_SHA256 = '55c44ebd735a0b00e6b436af5ee0edda0d2dc5a9304dc922ac028a1457687ac4'
SUPERVISOR_SCRIPT = Path('/tmp/opengenius-v16-recover-and-continue-v01.py')
SUPERVISOR_SCRIPT_SHA256 = '55c44ebd735a0b00e6b436af5ee0edda0d2dc5a9304dc922ac028a1457687ac4'
PRESERVATION_SCRIPT = Path('/tmp/opengenius-v16-recover-and-continue-v01.py')
PRESERVATION_SCRIPT_SHA256 = '55c44ebd735a0b00e6b436af5ee0edda0d2dc5a9304dc922ac028a1457687ac4'
AUDITOR = ROOT / 'scripts/audit_final_ytmusic_source_v23.py'
PREFLIGHT = ROOT / 'scripts/preflight_genius_package_v22.py'
UPDATER = ROOT / 'scripts/update_genius_bundle.py'
SAMPLE_SELECTOR = ROOT / 'scripts/select_final_musiccore_samples_v01.py'
INDEX_AUDITOR = ROOT / 'scripts/audit_index_expansion.py'
TOPOLOGY_AUDITOR = ROOT / 'scripts/audit_genius_graph_topology_v01.py'
MANIFEST_WRITER = ROOT / 'scripts/write_genius_package_manifest_v02.py'
BASELINE_PROJECTOR_REPORT = Path('/tmp/opengenius-native-baseline-title-map-v02.json')
BASELINE_PROJECTOR_REPORT_SHA256 = '1c2900f15904574751f3c974ccd0bf44660a51677a04b62cf16d41693517e973'
BASELINE_STATE = Path('/tmp/opengenius-native-baseline-title-map-v02-state.json')
BASELINE_STATE_SHA256 = '8e49d20c25ab4c504bbd1e105b0422aaf8feddc713e21005f9bfc481534ea3bd'
ORIGINAL_BUNDLE = ROOT / 'data/new-library-rebased-20261009-v03/Music Library.musiclibrary'
TRACK_SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
IDENTITY_MAP = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02/reviewed-shiny-ost-title-aliases-v01/identity-map.json'
MUSIC_EXECUTABLE = Path('/mnt/data/mizoyuzu/Music.app/Contents/MacOS/Music')
PACKAGE_DIR = ROOT / 'data/ytmusic/genius-bundle-total-genuine-1000-v01'
AUDIT_DIR = ROOT / 'data/ytmusic/final-source-audits-20261015-v23'
AUDIT_OUTPUT = AUDIT_DIR / 'final-source-audit-v23.json'
PREFLIGHT_OUTPUT = PACKAGE_DIR / 'package-preflight-v22.json'
SAMPLES_OUTPUT = PACKAGE_DIR / 'musiccore-sample-roots.json'
INDEX_OUTPUT = PACKAGE_DIR / 'audit-index-expansion.json'
TOPOLOGY_OUTPUT = PACKAGE_DIR / 'graph-topology-audit.json'
PACKAGE_MANIFEST_OUTPUT = PACKAGE_DIR / 'final-hash-manifest.json'
ORCHESTRATOR_RECEIPT = Path('/tmp/opengenius-final-genius-package-orchestration-v03.json')
MINIMUM_ROOTS = 1000


class GateError(ValueError):
    """The terminal or source/package contract is not ready."""


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def active_graph_rows(graph: dict) -> list[dict]:
    rows = graph.get('accepted_roots')
    require(isinstance(rows, list), 'final accepted graph has no root rows')
    pids = set()
    active = []
    for row in rows:
        require(isinstance(row, dict)
                and isinstance(row.get('root_pid'), str)
                and row['root_pid']
                and isinstance(row.get('ordered_target_pids'), list),
                'final accepted graph has a malformed PID vector')
        require(row['root_pid'] not in pids, 'final accepted graph repeats a PID row')
        pids.add(row['root_pid'])
        targets = row['ordered_target_pids']
        require(all(isinstance(pid, str) and pid for pid in targets),
                'final accepted graph has a malformed target PID')
        if targets:
            active.append(row)
    return active


def validate_checkpoint(progress_path: Path, terminal_path: Path,
                        expected_runtime_manifest_sha256: str = RUNTIME_MANIFEST_SHA256) -> dict:
    """Require the actual v16 continuation and >=1000 accepted graph roots."""
    progress_path = Path(progress_path).resolve()
    terminal_path = Path(terminal_path).resolve()
    require(progress_path.is_file(), 'campaign progress is missing')
    require(terminal_path.is_file(), 'v16 recovery-continuation terminal is missing')
    terminal = read_json(terminal_path)
    progress_hash = sha(progress_path)
    require(terminal.get('status') == 'completed'
            and type(terminal.get('returncode')) is int
            and terminal['returncode'] == 0,
            'v16 recovery-continuation terminal is not completed with rc=0')
    require(terminal.get('progress_sha256') == progress_hash,
            'supervisor terminal progress hash does not match current progress bytes')
    if terminal.get('progress_path') is not None:
        require(Path(terminal['progress_path']).resolve() == progress_path,
                'supervisor terminal identifies a different progress file')

    progress = read_json(progress_path)
    require(progress.get('status') == 'target_reached',
            'campaign progress must retain the expander target_reached terminal status')
    require(type(progress.get('successful_outgoing_roots')) is int
            and progress['successful_outgoing_roots'] >= MINIMUM_ROOTS,
            'campaign progress is below 1000 accepted roots')
    batches = progress.get('batches')
    require(isinstance(batches, list) and batches,
            'campaign progress has no completed collection batches')
    growth_dir = Path(batches[-1].get('growth_output', '')).resolve()
    require(growth_dir.is_dir() and growth_dir.is_relative_to(progress_path.parent),
            'last accepted growth directory is missing or outside this campaign')
    graph_path = growth_dir / 'accepted-graphs.json'
    receipt = batches[-1].get('growth_receipt')
    require(isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'}
            and Path(receipt['path']).resolve() == graph_path
            and graph_path.is_file() and sha(graph_path) == receipt['sha256'],
            'last progress graph receipt does not bind the final accepted graph')
    graph = read_json(graph_path)
    active = active_graph_rows(graph)
    require(len(active) >= MINIMUM_ROOTS
            and len(active) == progress['successful_outgoing_roots'],
            'accepted graph active roots differ from progress or are below 1000')
    terminal_graph_hash = terminal.get('accepted_graphs_sha256')
    require(terminal_graph_hash == receipt['sha256'],
            'supervisor terminal graph hash differs from the accepted progress receipt')
    invocation = _validate_invocation_lineage(progress, progress_path,
                                              expected_runtime_manifest_sha256)
    require(terminal.get('runtime_manifest_sha256') == expected_runtime_manifest_sha256
            and terminal.get('invocation_sha256') == invocation['invocation_sha256'],
            'v16 terminal does not bind the sealed runtime and exact invocation receipt')
    return {
        'terminal_path': str(terminal_path), 'terminal_sha256': sha(terminal_path),
        'progress_path': str(progress_path), 'progress_sha256': progress_hash,
        'growth_dir': str(growth_dir), 'accepted_graph_path': str(graph_path),
        'accepted_graph_sha256': receipt['sha256'], 'active_roots': len(active),
        'progress_status': progress['status'], 'supervisor_status': terminal['status'],
        'runtime_manifest_sha256': expected_runtime_manifest_sha256,
        'invocation_lineage': invocation,
    }


def _validate_invocation_lineage(progress: dict, progress_path: Path,
                                 expected_runtime_manifest_sha256: str) -> dict:
    """Bind final progress to v16 and preserve failed v15 as zero-credit history."""
    paths = (CONTINUATION_INVOCATION, SUPERVISOR_PROCESS, RECOVERY_PROGRESS_CHECKPOINT,
             PRESERVATION_AUDIT, RUNTIME_MANIFEST, RECOVERY_SCRIPT,
             V13_FAILED_TERMINAL, V13_FAILED_CHECKPOINT, V13_FAILED_INVOCATION,
             V15_FAILED_TERMINAL, V15_FAILED_PROCESS, V15_FAILED_INVOCATION, V15_RECOVERY_SCRIPT)
    require(all(path.is_file() for path in paths), 'v16 process and predecessor lineage receipts are incomplete')
    require(sha(RUNTIME_MANIFEST) == expected_runtime_manifest_sha256
            and expected_runtime_manifest_sha256 == RUNTIME_MANIFEST_SHA256,
            'sealed v15 runtime manifest changed')
    manifest = read_json(RUNTIME_MANIFEST)
    rows = manifest.get('modules')
    require(isinstance(rows, list) and len(rows) == 184, 'sealed v15 runtime manifest has an unexpected module list')
    expander_path = RUNTIME_ROOT / 'scripts/expand_genuine_roots.py'
    expander_row = next((row for row in rows if isinstance(row, dict)
                         and row.get('path') == 'scripts/expand_genuine_roots.py'), None)
    require(isinstance(expander_row, dict) and expander_path.is_file()
            and expander_row.get('sha256') == sha(expander_path),
            'v15 expander is not sealed by the pinned runtime')

    old_terminal = read_json(V13_FAILED_TERMINAL)
    old_checkpoint = read_json(V13_FAILED_CHECKPOINT)
    require(old_terminal.get('status') == 'failed' and old_terminal.get('returncode') == 1
            and old_terminal.get('progress_sha256') == sha(V13_FAILED_CHECKPOINT)
            and sha(V13_FAILED_CHECKPOINT) ==
            '4bbd6e564c289ee54618ce97432e51fdc7bc043960e7fe120e30c66d6dafa356'
            and old_checkpoint.get('successful_outgoing_roots') == 826,
            'v13 failed checkpoint is not preserved')
    v15_inv, v15_proc, v15_term = map(read_json, (V15_FAILED_INVOCATION, V15_FAILED_PROCESS, V15_FAILED_TERMINAL))
    require(sha(V15_RECOVERY_SCRIPT) == V15_RECOVERY_SCRIPT_SHA256
            and v15_inv.get('script_sha256') == V15_RECOVERY_SCRIPT_SHA256
            and v15_inv.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v15_inv.get('progress_before_sha256') == sha(V13_FAILED_CHECKPOINT)
            and v15_proc.get('script_sha256') == V15_RECOVERY_SCRIPT_SHA256
            and v15_proc.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and set(v15_proc) == set(v15_inv) | {'started_unix', 'worker_pid'}
            and all(v15_proc.get(k) == v for k, v in v15_inv.items())
            and type(v15_proc.get('worker_pid')) is int and v15_proc['worker_pid'] > 0
            and v15_term.get('status') == 'failed' and v15_term.get('returncode') == 1
            and v15_term.get('progress_sha256') == sha(V13_FAILED_CHECKPOINT)
            and v15_term.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and v15_term.get('invocation_sha256') == sha(V15_FAILED_INVOCATION),
            'stopped v15 attempt is not preserved as a failed, uncredited run')

    invocation = read_json(CONTINUATION_INVOCATION)
    process = read_json(SUPERVISOR_PROCESS)
    terminal = read_json(SUPERVISOR_TERMINAL)
    preservation = read_json(PRESERVATION_AUDIT)
    recovered = read_json(RECOVERY_PROGRESS_CHECKPOINT)
    require(sha(RECOVERY_SCRIPT) == RECOVERY_SCRIPT_SHA256
            and process.get('script_sha256') == RECOVERY_SCRIPT_SHA256
            and process.get('runtime_manifest_sha256') == expected_runtime_manifest_sha256
            and set(process) == set(invocation) | {'started_unix', 'worker_pid'}
            and all(process.get(k) == v for k, v in invocation.items())
            and type(process.get('worker_pid')) is int and process['worker_pid'] > 0,
            'v16 process receipt does not bind the exact invocation')
    require(invocation.get('runtime_manifest_sha256') == expected_runtime_manifest_sha256
            and invocation.get('script_sha256') == RECOVERY_SCRIPT_SHA256
            and invocation.get('source_v05_invocation_sha256') == sha(V13_FAILED_INVOCATION)
            and invocation.get('progress_before_sha256') == sha(V13_FAILED_CHECKPOINT),
            'v16 invocation does not descend from the unchanged 826-root progress')
    require(terminal.get('status') == 'completed' and terminal.get('returncode') == 0
            and terminal.get('runtime_manifest_sha256') == expected_runtime_manifest_sha256
            and terminal.get('invocation_sha256') == sha(CONTINUATION_INVOCATION)
            and terminal.get('pretransport_gate_passed') is True
            and terminal.get('progress_sha256') == sha(progress_path)
            and terminal.get('accepted_graphs_sha256') == progress.get('batches', [{}])[-1].get('growth_receipt', {}).get('sha256')
            and terminal.get('progress_status') == 'target_reached'
            and terminal.get('roots') == progress.get('successful_outgoing_roots')
            and terminal.get('http_requests_total') == progress.get('http_requests')
            and terminal.get('attempted_pids') == len(progress.get('attempted_pids', [])),
            'v16 terminal does not bind the final target-reached progress')
    require(recovered.get('successful_outgoing_roots') == 840
            and recovered.get('http_requests') == 4225
            and len(recovered.get('attempted_pids', [])) == 1925
            and preservation.get('status') == 'passed_recovered_checkpoint_preservation_before_new_transport'
            and preservation.get('progress_sha256') == sha(RECOVERY_PROGRESS_CHECKPOINT)
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
            'v16 recovered checkpoint or exact parent-row promotion guard changed')
    argv = invocation.get('argv')
    require(isinstance(argv, list) and len(argv) > 2 and all(isinstance(x, str) for x in argv)
            and argv[0] == str(ROOT / '.venv/bin/python')
            and Path(argv[1]).resolve() == expander_path.resolve(),
            'v16 invocation does not execute the exact sealed expander')
    args = argv[2:]
    parsed = {}
    i = 0
    while i < len(args):
        require(args[i].startswith('--') and i + 1 < len(args) and args[i] not in parsed,
                'v16 invocation argv is malformed')
        parsed[args[i]] = args[i + 1]
        i += 2
    expected = {'--target-outgoing-roots': '1000', '--global-request-budget': '6500',
                '--max-attempts': '2800', '--batch-size': '44',
                '--batch-request-budget': '200', '--interval': '3',
                '--candidate-limit': '100', '--stop-after-batches': '20',
                '--source-policy': 'authenticated-watch-radio-only-v1',
                '--collector-collection-mode': 'radio-only-v1',
                '--output': str(progress_path.resolve().parent)}
    require(all(parsed.get(k) == v for k, v in expected.items()),
            'v16 invocation changed the approved caps, policy, pacing, or output')
    config = progress.get('config')
    require(isinstance(config, dict) and config.get('target_outgoing_roots') == 1000
            and config.get('global_request_budget') == 6500 and config.get('max_attempts') == 2800
            and config.get('batch_size') == 44 and config.get('batch_request_budget') == 200
            and config.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and config.get('collector_collection_mode') == 'radio-only-v1'
            and config.get('authorized_retry_release_pids') == [],
            'progress config changed the authorized v16 policy or limits')
    return {'v15_failed_terminal_sha256': sha(V15_FAILED_TERMINAL),
            'invocation_sha256': sha(CONTINUATION_INVOCATION),
            'v16_invocation_sha256': sha(CONTINUATION_INVOCATION),
            'v16_process_sha256': sha(SUPERVISOR_PROCESS),
            'v16_terminal_sha256': sha(SUPERVISOR_TERMINAL),
            'recovered_checkpoint_sha256': sha(RECOVERY_PROGRESS_CHECKPOINT),
            'preservation_sha256': sha(PRESERVATION_AUDIT),
            'runtime_manifest_sha256': expected_runtime_manifest_sha256,
            'worker_pid': process['worker_pid']}


def validate_original_inventory() -> dict:
    """Confirm all twelve physical bundle members still match the reviewed baseline receipt."""
    require(BASELINE_PROJECTOR_REPORT.is_file()
            and sha(BASELINE_PROJECTOR_REPORT) == BASELINE_PROJECTOR_REPORT_SHA256,
            'Native baseline projection report is missing or changed')
    report = read_json(BASELINE_PROJECTOR_REPORT)
    inventory = report.get('physical_bundle', {}).get('file_sha256')
    require(report.get('physical_sql_matches_baseline') is True
            and report.get('source_bundle_unchanged_on_disk') is True
            and isinstance(inventory, dict) and len(inventory) == 12,
            'baseline report does not prove the exact 12-file physical bundle inventory')
    actual_paths = sorted(path.relative_to(ORIGINAL_BUNDLE).as_posix()
                          for path in ORIGINAL_BUNDLE.rglob('*') if path.is_file())
    require(actual_paths == sorted(inventory),
            'physical source bundle membership differs from its original 12-file inventory')
    actual = {name: sha(ORIGINAL_BUNDLE / name) for name in actual_paths}
    require(actual == inventory, 'physical source bundle bytes changed from the reviewed baseline')
    require(BASELINE_STATE.is_file(), 'projected Native baseline state is missing')
    require(sha(BASELINE_STATE) == BASELINE_STATE_SHA256,
            'projected Native baseline state changed from the reviewed receipt')
    return {'bundle_path': str(ORIGINAL_BUNDLE), 'file_count': len(actual),
            'projection_report_path': str(BASELINE_PROJECTOR_REPORT),
            'projection_report_sha256': BASELINE_PROJECTOR_REPORT_SHA256,
            'file_sha256': actual}


def _command_runner(argv: list[str], **kwargs):
    require(isinstance(argv, list) and all(isinstance(part, str) for part in argv),
            'orchestrator child commands must use structured string argv')
    kwargs.pop('shell', None)
    return subprocess.run(argv, shell=False, **kwargs)


def _run_json_command(argv: list[str], *, cwd: Path, runner, stage: str) -> dict:
    result = runner(argv, cwd=str(cwd), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GateError(f'{stage} failed with rc={result.returncode}: {result.stderr[-2000:]}')
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise GateError(f'{stage} did not return a JSON object') from error


def _write_json_new(path: Path, document: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')


def _tree_receipts(directory: Path) -> dict[str, str]:
    rows = {}
    for path in sorted(Path(directory).rglob('*')):
        require(not path.is_symlink(), 'package output contains a symlink')
        if path.is_file():
            rows[path.relative_to(directory).as_posix()] = sha(path)
    return rows


def _output_artifacts() -> set[str]:
    """Enumerate only the orchestrator's declared output roots for honest failure reporting."""
    found: set[str] = set()
    for root in (AUDIT_DIR, PACKAGE_DIR, ORCHESTRATOR_RECEIPT):
        if not root.exists():
            continue
        found.add(str(root.resolve()))
        if root.is_dir():
            found.update(str(path.resolve()) for path in root.rglob('*'))
    return found


def orchestrate(*, progress_path: Path = PROGRESS,
                terminal_path: Path = SUPERVISOR_TERMINAL,
                runner=_command_runner) -> dict:
    """Execute only after gates; any source-audit failure prevents the copy command."""
    checkpoint = validate_checkpoint(progress_path, terminal_path,
                                     expected_runtime_manifest_sha256=RUNTIME_MANIFEST_SHA256)
    progress_parent = Path(progress_path).resolve().parent
    require(not AUDIT_OUTPUT.resolve().is_relative_to(progress_parent),
            'source audit output must be outside the frozen live campaign directory')
    preexisting = (AUDIT_OUTPUT, AUDIT_OUTPUT.with_name(AUDIT_OUTPUT.stem + '.receipt.json'),
                   AUDIT_DIR,
                   PACKAGE_DIR, ORCHESTRATOR_RECEIPT)
    require(all(not path.exists() for path in preexisting),
            'a final audit/package output already exists; refusing overwrite')
    original_inventory = validate_original_inventory()

    code_paths = (Path(__file__).resolve(), AUDITOR, PREFLIGHT, UPDATER, SAMPLE_SELECTOR,
                  INDEX_AUDITOR, TOPOLOGY_AUDITOR, MANIFEST_WRITER,
                  RECOVERY_SCRIPT, SUPERVISOR_SCRIPT, PRESERVATION_SCRIPT)
    code_hashes = {str(path): sha(path) for path in code_paths}
    require(sha(RUNTIME_MANIFEST) == RUNTIME_MANIFEST_SHA256,
            'frozen v15 runtime manifest changed')

    audit_argv = [sys.executable, str(AUDITOR), '--progress', str(progress_path),
                  '--output-audit', str(AUDIT_OUTPUT), '--metric', 'total-genuine']
    audit_summary = _run_json_command(audit_argv, cwd=ROOT, runner=runner,
                                      stage='independent un-memoized whole-source audit')
    require(audit_summary.get('status') == 'passed'
            and type(audit_summary.get('outgoing_root_count')) is int
            and audit_summary['outgoing_root_count'] >= MINIMUM_ROOTS,
            'whole-source audit did not pass the total-genuine 1000-root gate')
    require(AUDIT_OUTPUT.is_file(), 'whole-source audit did not write its report')
    audit_receipt_path = AUDIT_OUTPUT.with_name(AUDIT_OUTPUT.stem + '.receipt.json')
    require(audit_receipt_path.is_file(), 'whole-source audit did not write its detached receipt')
    audit_doc, audit_receipt = read_json(AUDIT_OUTPUT), read_json(audit_receipt_path)
    growth_dir = Path(audit_doc.get('independent_full_chain_replay', {}).get('checkpoint', '')).resolve()
    audit_state_path = growth_dir / 'state.json'
    require(audit_doc.get('status') == 'passed'
            and audit_doc.get('independent_full_chain_replay', {}).get('status') == 'passed'
            and audit_doc.get('source_root_origin', {}).get('goal_metric') == 'total-genuine'
            and type(audit_doc.get('source_root_origin', {}).get('counts', {}).get('total_active')) is int
            and audit_doc.get('source_root_origin', {}).get('counts', {}).get('total_active') >= MINIMUM_ROOTS
            and audit_doc.get('source_root_origin', {}).get('counts', {}).get('unknown') == 0
            and audit_receipt.get('source_audit_sha256') == sha(AUDIT_OUTPUT)
            and audit_receipt.get('source_audit_path') == str(AUDIT_OUTPUT.resolve())
            and Path(audit_receipt.get('checkpoint_path', '')).resolve() == growth_dir
            and audit_receipt.get('accepted_graphs_sha256') == checkpoint['accepted_graph_sha256']
            and audit_receipt.get('state_file_sha256') == sha(audit_state_path)
            and audit_doc.get('final_files_sha256', {}).get('state') == sha(audit_state_path)
            and audit_receipt.get('runtime_manifest_sha256') == RUNTIME_MANIFEST_SHA256
            and audit_doc.get('final_files_sha256', {}).get('progress') == checkpoint['progress_sha256'],
            'whole-source audit report or detached receipt does not satisfy the exact final gate')
    require(sha(progress_path) == checkpoint['progress_sha256'],
            'progress bytes changed while the whole-source audit ran')
    require(growth_dir == Path(checkpoint['growth_dir']).resolve(),
            'auditor selected a different checkpoint than the supervisor terminal')

    baseline_only = _run_json_command(
        [sys.executable, str(PREFLIGHT), '--baseline-only'], cwd=ROOT,
        runner=runner, stage='Native physical baseline preflight')
    require(baseline_only.get('library_track_count') == 3004
            and baseline_only.get('baseline_assignments') == 515
            and baseline_only.get('baseline_ordered_edges') == 3341,
            'Native baseline preflight did not reproduce the fixed physical baseline')
    preflight_argv = [sys.executable, str(PREFLIGHT), '--growth-dir', str(growth_dir),
                      '--source-audit', str(AUDIT_OUTPUT), '--metric', 'total-genuine']
    preflight = _run_json_command(preflight_argv, cwd=ROOT, runner=runner,
                                  stage='final source/package preflight')
    require(preflight.get('status') == 'ready_for_separate_copy_packaging'
            and preflight.get('qualifying_roots', 0) >= MINIMUM_ROOTS
            and preflight.get('unknown_root_origins') == 0,
            'package preflight rejected the final source audit')

    updater_argv = [sys.executable, str(UPDATER), '--bundle', str(ORIGINAL_BUNDLE),
                    '--track-snapshot', str(TRACK_SNAPSHOT), '--identity-map', str(IDENTITY_MAP),
                    '--evaluation', str(growth_dir / 'accepted-evaluation.json'),
                    '--baseline-state', str(BASELINE_STATE), '--executable', str(MUSIC_EXECUTABLE),
                    '--output', str(PACKAGE_DIR), '--allow-unverified-profile']
    update_summary = _run_json_command(updater_argv, cwd=ROOT, runner=runner,
                                       stage='separate Genius bundle copy')
    require(PACKAGE_DIR.is_dir(), 'bundle updater did not create the separate package directory')
    for name in ('report.json', 'plan.json', 'state.json', 'Music Library.musiclibrary'):
        require((PACKAGE_DIR / name).exists(), 'bundle updater omitted ' + name)

    _write_json_new(PREFLIGHT_OUTPUT, preflight)
    selector_argv = [sys.executable, str(SAMPLE_SELECTOR), '--graph',
                     str(growth_dir / 'accepted-graphs.json'), '--source-audit', str(AUDIT_OUTPUT),
                     '--native-state', str(Path('/tmp/opengenius-final-campaign-native-v13-child-v04') / 'baseline-state.json'),
                     '--output', str(SAMPLES_OUTPUT)]
    selector_summary = _run_json_command(selector_argv, cwd=ROOT, runner=runner,
                                         stage='MusicCore sample selection')
    samples = read_json(SAMPLES_OUTPUT)
    sample_pids = samples.get('sample_pids')
    require(samples.get('sample_count') == 5 and isinstance(sample_pids, list)
            and len(sample_pids) == 5 and len(set(sample_pids)) == 5,
            'sample selector did not produce exactly five distinct PIDs')

    index_argv = [sys.executable, str(INDEX_AUDITOR), '--baseline-bundle', str(ORIGINAL_BUNDLE),
                  '--output-bundle', str(PACKAGE_DIR / 'Music Library.musiclibrary'),
                  '--baseline-state', str(BASELINE_STATE), '--output-state', str(PACKAGE_DIR / 'state.json'),
                  '--track-snapshot', str(TRACK_SNAPSHOT), '--identity-map', str(IDENTITY_MAP),
                  '--executable', str(MUSIC_EXECUTABLE), '--output', str(INDEX_OUTPUT),
                  '--require-sync-revisions']
    for pid in sample_pids:
        index_argv.extend(['--sample-root', pid])
    index_summary = _run_json_command(index_argv, cwd=ROOT, runner=runner,
                                      stage='copied SQL/index/sync/MusicCore audit')
    index_doc = read_json(INDEX_OUTPUT)
    require(index_doc.get('sqlite_integrity') == {'baseline': 'ok', 'output': 'ok'}
            and index_doc.get('baseline_library_track_count') == 3004
            and index_doc.get('output_library_track_count') == 3004
            and index_doc.get('existing_pid_count') == 515
            and index_doc.get('existing_pid_gid_and_metadata_preserved') is True
            and index_doc.get('sync_revision_mismatch_count') == 0
            and len(index_doc.get('music_core_sample') or []) >= 5,
            'copied SQL/index/sync/MusicCore audit failed a required invariant')

    topology_argv = [sys.executable, str(TOPOLOGY_AUDITOR), '--graph',
                     str(growth_dir / 'accepted-graphs.json'), '--state', str(PACKAGE_DIR / 'state.json'),
                     '--output-bundle', str(PACKAGE_DIR / 'Music Library.musiclibrary'),
                     '--music-executable', str(MUSIC_EXECUTABLE), '--minimum-active-roots', '1000',
                     '--output', str(TOPOLOGY_OUTPUT)]
    topology_summary = _run_json_command(topology_argv, cwd=ROOT, runner=runner,
                                         stage='accepted graph to copied SQL topology audit')
    topology_doc = read_json(TOPOLOGY_OUTPUT)
    require(topology_doc.get('status') == 'passed'
            and topology_doc.get('accepted_pid_vectors_match_output_state_and_sql') is True
            and topology_doc.get('state_only_assignments_are_empty') is True
            and topology_doc.get('active_graph_roots') >= MINIMUM_ROOTS,
            'accepted graph does not match the copied ordered SQL relation vectors')

    manifest_argv = [sys.executable, str(MANIFEST_WRITER), '--package-dir', str(PACKAGE_DIR),
                     '--growth-dir', str(growth_dir), '--progress', str(progress_path),
                     '--source-audit', str(AUDIT_OUTPUT), '--source-audit-receipt', str(audit_receipt_path),
                     '--source-preflight', str(PREFLIGHT_OUTPUT), '--index-audit', str(INDEX_OUTPUT),
                     '--graph-topology-audit', str(TOPOLOGY_OUTPUT),
                     '--baseline-projection', str(BASELINE_PROJECTOR_REPORT),
                     '--baseline-state', str(BASELINE_STATE), '--original-bundle', str(ORIGINAL_BUNDLE),
                     '--identity-map', str(IDENTITY_MAP), '--track-snapshot', str(TRACK_SNAPSHOT),
                     '--music-executable', str(MUSIC_EXECUTABLE), '--output', str(PACKAGE_MANIFEST_OUTPUT)]
    for code in (AUDITOR, PREFLIGHT, ROOT / 'scripts/prepare_native_baseline_for_title_map_v01.py',
                 SAMPLE_SELECTOR, MANIFEST_WRITER, UPDATER, INDEX_AUDITOR, TOPOLOGY_AUDITOR):
        manifest_argv.extend(['--code', str(code)])
    manifest_summary = _run_json_command(manifest_argv, cwd=ROOT, runner=runner,
                                         stage='final package hash closure')

    output_hashes = _tree_receipts(PACKAGE_DIR)
    input_hashes = {
        'supervisor_terminal': {str(terminal_path.resolve()): sha(terminal_path)},
        'progress': {str(Path(progress_path).resolve()): sha(progress_path)},
        'runtime_manifest': {str(RUNTIME_MANIFEST): sha(RUNTIME_MANIFEST)},
        'continuation_invocation_lineage': {
            str(path): sha(path) for path in (SUPERVISOR_PROCESS, CONTINUATION_INVOCATION,
                                              CONTINUATION_CHILD, RECOVERY_PROCESS,
                                              RECOVERY_TERMINAL, RECOVERY_PROGRESS_CHECKPOINT,
                                              PRESERVATION_AUDIT)},
        'original_bundle_files': original_inventory['file_sha256'],
        'snapshot': {str(TRACK_SNAPSHOT): sha(TRACK_SNAPSHOT)},
        'identity_map': {str(IDENTITY_MAP): sha(IDENTITY_MAP)},
        'music_executable': {str(MUSIC_EXECUTABLE): sha(MUSIC_EXECUTABLE)},
        'baseline_state': {str(BASELINE_STATE): sha(BASELINE_STATE)},
        'source_audit_closure': audit_doc.get('source_closure_sha256', {}),
    }
    receipt = {
        'schema_version': 1,
        'producer': 'opengenius-final-genius-package-orchestrator-v3',
        'status': 'completed',
        'metric': 'total-genuine',
        'active_roots': checkpoint['active_roots'],
        'supervisor_terminal': checkpoint,
        'source_audit': {'path': str(AUDIT_OUTPUT), 'sha256': sha(AUDIT_OUTPUT),
                         'receipt_sha256': sha(audit_receipt_path)},
        'package_preflight': {'path': str(PREFLIGHT_OUTPUT), 'sha256': sha(PREFLIGHT_OUTPUT)},
        'index_audit': {'path': str(INDEX_OUTPUT), 'sha256': sha(INDEX_OUTPUT)},
        'graph_topology_audit': {'path': str(TOPOLOGY_OUTPUT), 'sha256': sha(TOPOLOGY_OUTPUT)},
        'final_package_manifest': {'path': str(PACKAGE_MANIFEST_OUTPUT),
                                   'sha256': sha(PACKAGE_MANIFEST_OUTPUT)},
        'code_sha256': code_hashes,
        'input_sha256': input_hashes,
        'package_output_files_sha256': output_hashes,
        'stage_summaries': {'source_audit': audit_summary, 'baseline_preflight': baseline_only,
                            'package_preflight': preflight, 'updater': update_summary,
                            'sample_selector': selector_summary, 'index_audit': index_summary,
                            'topology_audit': topology_summary,
                            'package_manifest': manifest_summary},
        'network_requests': 0,
        'music_app_launched': False,
    }
    _write_json_new(ORCHESTRATOR_RECEIPT, receipt)
    return {'status': 'completed', 'active_roots': checkpoint['active_roots'],
            'source_audit_sha256': sha(AUDIT_OUTPUT),
            'package_manifest_sha256': sha(PACKAGE_MANIFEST_OUTPUT),
            'orchestrator_receipt': str(ORCHESTRATOR_RECEIPT),
            'orchestrator_receipt_sha256': sha(ORCHESTRATOR_RECEIPT)}


def main() -> int:
    before = _output_artifacts()
    try:
        result = orchestrate()
    except GateError as error:
        written = sorted(_output_artifacts() - before)
        print(json.dumps({'status': 'refused', 'reason': str(error),
                          'artifact_writes': written,
                          'artifact_write_count': len(written)}, sort_keys=True))
        return 2
    except Exception as error:
        written = sorted(_output_artifacts() - before)
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'message': str(error), 'artifact_writes': written,
                          'artifact_write_count': len(written)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
