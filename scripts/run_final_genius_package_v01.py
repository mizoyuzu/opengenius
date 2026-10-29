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
SUPERVISOR_TERMINAL = CAMPAIGN / 'v13-continuation-supervisor-terminal-v05.json'
SUPERVISOR_PROCESS = CAMPAIGN / 'v13-continuation-supervisor-process-v05.json'
CONTINUATION_INVOCATION = CAMPAIGN / 'v13-continuation-invocation-v05.json'
CONTINUATION_CHILD = CAMPAIGN / 'v13-continuation-child-v05.json'
RECOVERY_PROCESS = CAMPAIGN / 'v13-recovery-process-v01.json'
RECOVERY_TERMINAL = CAMPAIGN / 'v13-recovery-terminal-v01.json'
RECOVERY_PROGRESS_CHECKPOINT = CAMPAIGN / 'v13-accepted826-progress-checkpoint-v01.json'
PRESERVATION_AUDIT = CAMPAIGN / 'independent-preservation-audit-v03.json'
RUNTIME_MANIFEST = Path('/tmp/opengenius-final-campaign-runtime-v13-typed-baseline-cache-v01/runtime-manifest.json')
RUNTIME_MANIFEST_SHA256 = 'c2162bc7e9d79f6ecb144df411d5ed7eaf4b72407aa52a69a047f148d0d459ef'
RUNTIME_ROOT = RUNTIME_MANIFEST.parent
RECOVERY_SCRIPT = Path('/tmp/opengenius-v13-recover44-no-http-v01.py')
RECOVERY_SCRIPT_SHA256 = '64961e7038ce374d6dbf020d285577d5a198f13670459852a31746975893dfa4'
SUPERVISOR_SCRIPT = Path('/tmp/opengenius-v13-resume-after-recovery-v05.py')
SUPERVISOR_SCRIPT_SHA256 = 'a300c1fa040f0be909c6f373b6f341473ceacd44b18cc30debec783460805295'
PRESERVATION_SCRIPT = Path('/tmp/opengenius-v13-postterminal-preservation-audit-v03.py')
PRESERVATION_SCRIPT_SHA256 = 'bebd72704ace50f778866adff732d36b291c7b38c77f1d6deb982198e054984f'
AUDITOR = ROOT / 'scripts/audit_final_ytmusic_source_v21.py'
PREFLIGHT = ROOT / 'scripts/preflight_genius_package_v20.py'
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
AUDIT_DIR = ROOT / 'data/ytmusic/final-source-audits-20261014-v21'
AUDIT_OUTPUT = AUDIT_DIR / 'final-source-audit-v21.json'
PREFLIGHT_OUTPUT = PACKAGE_DIR / 'package-preflight-v20.json'
SAMPLES_OUTPUT = PACKAGE_DIR / 'musiccore-sample-roots.json'
INDEX_OUTPUT = PACKAGE_DIR / 'audit-index-expansion.json'
TOPOLOGY_OUTPUT = PACKAGE_DIR / 'graph-topology-audit.json'
PACKAGE_MANIFEST_OUTPUT = PACKAGE_DIR / 'final-hash-manifest.json'
ORCHESTRATOR_RECEIPT = Path('/tmp/opengenius-final-genius-package-orchestration-v01.json')
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
    """Require the actual v05-supervised v13 checkpoint and >=1000 accepted graph roots."""
    progress_path = Path(progress_path).resolve()
    terminal_path = Path(terminal_path).resolve()
    require(progress_path.is_file(), 'campaign progress is missing')
    require(terminal_path.is_file(), 'v13 continuation supervisor terminal v02 is missing')
    terminal = read_json(terminal_path)
    progress_hash = sha(progress_path)
    require(terminal.get('status') == 'completed'
            and type(terminal.get('returncode')) is int
            and terminal['returncode'] == 0,
            'v05 continuation supervisor terminal is not completed with rc=0')
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
            'v05 terminal does not bind the sealed runtime and exact invocation receipt')
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
    """Bind the minimal supervisor terminal to its real process and exact v13 argv receipts."""
    paths = (SUPERVISOR_PROCESS, CONTINUATION_INVOCATION, CONTINUATION_CHILD,
             RECOVERY_PROCESS, RECOVERY_TERMINAL, RECOVERY_PROGRESS_CHECKPOINT,
             PRESERVATION_AUDIT, RUNTIME_MANIFEST, RECOVERY_SCRIPT, SUPERVISOR_SCRIPT,
             PRESERVATION_SCRIPT)
    require(all(path.is_file() for path in paths),
            'v13 process/invocation/recovery lineage receipts are incomplete')
    manifest_hash = sha(RUNTIME_MANIFEST)
    require(manifest_hash == expected_runtime_manifest_sha256,
            'sealed v13 runtime manifest changed')
    manifest = read_json(RUNTIME_MANIFEST)
    rows = manifest.get('modules')
    require(isinstance(rows, list), 'sealed v13 runtime manifest has no module list')
    expander_row = next((row for row in rows
                         if isinstance(row, dict)
                         and row.get('path') == 'scripts/expand_genuine_roots.py'), None)
    expander_path = RUNTIME_ROOT / 'scripts/expand_genuine_roots.py'
    require(isinstance(expander_row, dict) and expander_path.is_file()
            and expander_row.get('sha256') == sha(expander_path),
            'v13 expander entrypoint is not sealed by the pinned manifest')

    supervisor = read_json(SUPERVISOR_PROCESS)
    recovery = read_json(RECOVERY_PROCESS)
    recovery_terminal = read_json(RECOVERY_TERMINAL)
    invocation = read_json(CONTINUATION_INVOCATION)
    child = read_json(CONTINUATION_CHILD)
    preservation = read_json(PRESERVATION_AUDIT)
    require(supervisor.get('runtime_manifest_sha256') == manifest_hash
            and type(supervisor.get('supervisor_pid')) is int
            and supervisor['supervisor_pid'] > 0
            and type(supervisor.get('recovery_worker_pid')) is int
            and supervisor['recovery_worker_pid'] > 0
            and supervisor.get('normal_recovery_terminal_required') is True
            and supervisor.get('extra_metadata_calls') == 0,
            'supervisor process receipt does not bind the frozen v13 run')
    require(recovery.get('runtime_manifest_sha256') == manifest_hash
            and type(recovery.get('worker_pid')) is int
            and recovery['worker_pid'] > 0
            and recovery.get('collector_invocations_permitted') == 0
            and recovery.get('baseline_substitutions') == 0
            and recovery.get('script_sha256') == RECOVERY_SCRIPT_SHA256
            and sha(RECOVERY_SCRIPT) == RECOVERY_SCRIPT_SHA256
            and sha(SUPERVISOR_SCRIPT) == SUPERVISOR_SCRIPT_SHA256,
            'normal recovery process receipt has an invalid runtime or scope pin')
    require(supervisor['recovery_worker_pid'] == recovery['worker_pid']
            and recovery_terminal.get('status') == 'completed'
            and recovery_terminal.get('returncode') == 0
            and recovery_terminal.get('new_http_requests') == 0
            and recovery_terminal.get('baseline_substitutions') == 0
            and recovery_terminal.get('runtime_manifest_sha256') == manifest_hash
            and recovery_terminal.get('progress_sha256') == sha(RECOVERY_PROGRESS_CHECKPOINT)
            and invocation.get('normal_recovery_terminal_sha256') == sha(RECOVERY_TERMINAL)
            and invocation.get('preserved826_progress_sha256') == sha(RECOVERY_PROGRESS_CHECKPOINT)
            and invocation.get('progress_before_sha256') == sha(RECOVERY_PROGRESS_CHECKPOINT)
            and invocation.get('independent_preservation_audit_sha256') == sha(PRESERVATION_AUDIT)
            and invocation.get('preservation_auditor_sha256') == PRESERVATION_SCRIPT_SHA256
            and sha(PRESERVATION_SCRIPT) == PRESERVATION_SCRIPT_SHA256
            and preservation.get('status') ==
            'passed_postterminal_preservation_checks_uncredited_pending_final_whole_source_audit'
            and preservation.get('worker', {}).get('progress_sha256') == sha(RECOVERY_PROGRESS_CHECKPOINT)
            and preservation.get('runtime_manifest', {}).get('sha256') == manifest_hash,
            'v13 invocation does not descend from the successful normal recovery and preservation gate')
    recorded_argv = invocation.get('argv')
    require(isinstance(recorded_argv, list) and len(recorded_argv) > 2
            and all(isinstance(arg, str) for arg in recorded_argv),
            'v13 invocation receipt has no exact string argv')
    require(recorded_argv[0] == str(ROOT / '.venv/bin/python')
            and Path(recorded_argv[1]).resolve() == expander_path.resolve(),
            'v13 invocation does not execute the sealed campaign entrypoint')
    require(type(invocation.get('maximum_completed_batches')) is int
            and invocation['maximum_completed_batches'] == 20
            and invocation.get('global_http_cap') == 6500
            and invocation.get('no_limits_raised') is True,
            'v13 invocation receipt has unexpected continuation limits')
    require(child.get('invocation_sha256') == sha(CONTINUATION_INVOCATION)
            and type(child.get('worker_pid')) is int and child['worker_pid'] > 0,
            'v13 child receipt does not bind the exact invocation file')

    config = progress.get('config')
    require(isinstance(config, dict), 'current progress has no run configuration')
    args = recorded_argv[2:]
    parsed = {}
    i = 0
    while i < len(args):
        key = args[i]
        require(key.startswith('--') and i + 1 < len(args),
                'v13 invocation argv has an unexpected flag shape')
        require(key not in parsed, 'v13 invocation argv repeats a configuration flag')
        parsed[key] = args[i + 1]
        i += 2
    config_flags = {
        '--auth': ('auth_path', None),
        '--track-snapshot': ('track_snapshot', 'path'),
        '--identity-map': ('identity_map', 'path'),
        '--baseline-state': ('baseline_state', 'path'),
        '--baseline-graph-export': ('baseline_graph_export', 'path'),
        '--rebase-report': ('rebase_report', 'path'),
        '--seed-video-evidence': ('external_seed_video_evidence', 'path'),
        '--prior-attempt-ledger': ('prior_attempt_ledger', 'path'),
    }
    for flag, (section, key) in config_flags.items():
        value = config.get(section)
        expected = value.get(key) if key is not None and isinstance(value, dict) else value
        require(expected is not None and parsed.get(flag) == expected,
                'recorded v13 argv differs from progress config: ' + flag)
    for flag, config_key in (
        ('--target-outgoing-roots', 'target_outgoing_roots'),
        ('--global-request-budget', 'global_request_budget'),
        ('--max-attempts', 'max_attempts'),
        ('--batch-size', 'batch_size'),
        ('--batch-request-budget', 'batch_request_budget'),
        ('--candidate-limit', 'candidate_limit'),
        ('--source-policy', 'source_policy'),
        ('--collector-collection-mode', 'collector_collection_mode'),
    ):
        require(flag in parsed and parsed[flag] == str(config.get(config_key)),
                'recorded v13 argv differs from progress config: ' + flag)
    try:
        recorded_interval = float(parsed.get('--interval', 'nan'))
    except (TypeError, ValueError):
        recorded_interval = math.nan
    try:
        configured_interval = float(config.get('interval'))
    except (TypeError, ValueError):
        configured_interval = math.nan
    require(parsed.get('--stop-after-batches') == str(invocation['maximum_completed_batches'])
            and math.isfinite(recorded_interval) and recorded_interval == configured_interval
            and config.get('authorized_retry_release_pids') == [],
            'recorded v13 argv changed the batch stop, pacing, or retry policy')
    require(config.get('target_outgoing_roots') == 1000
            and config.get('global_request_budget') == 6500
            and config.get('max_attempts') == 2800
            and config.get('batch_size') == 44
            and config.get('batch_request_budget') == 200
            and config.get('candidate_limit') == 100
            and config.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and config.get('collector_collection_mode') == 'radio-only-v1',
            'progress config does not preserve the authorized v13 continuation limits/policy')
    require(Path(parsed.get('--output', '')).resolve() == progress_path.resolve().parent,
            'recorded v13 argv writes to a different campaign directory')
    return {
        'supervisor_process_sha256': sha(SUPERVISOR_PROCESS),
        'recovery_process_sha256': sha(RECOVERY_PROCESS),
        'recovery_terminal_sha256': sha(RECOVERY_TERMINAL),
        'preservation_audit_sha256': sha(PRESERVATION_AUDIT),
        'invocation_sha256': sha(CONTINUATION_INVOCATION),
        'child_sha256': sha(CONTINUATION_CHILD),
        'entrypoint_path': str(expander_path),
        'entrypoint_sha256': expander_row['sha256'],
        'runtime_manifest_sha256': manifest_hash,
        'preservation_script_sha256': sha(PRESERVATION_SCRIPT),
        'argv_sha256': hashlib.sha256(json.dumps(recorded_argv, separators=(',', ':')).encode()).hexdigest(),
        'worker_pid': child['worker_pid'],
    }


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
            'frozen v13 runtime manifest changed')

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
        'producer': 'opengenius-final-genius-package-orchestrator-v1',
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
