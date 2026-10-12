"""Typed, source-bound child rebind for ordered append-only album aliases."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from music_identity_map import IdentityMap, load_library
from plan_genius_update import _hash as state_hash, plan_genius_update
from update_genius_bundle import state_rows
from rebind_radio_growth_alias_chain import (
    _canonical_receipt_rows, _proof_input_receipts, _read, _receipt,
    _relation_map, _rebind_state_map_hash, _state_graph_rows,
    _validate_state_graph_relation_projection,
    verify_alias_chain_growth_rebinding_report, verify_album_alias_chain_growth_rebinding_report,
    write_json,
)
from append_reviewed_album_title_aliases import verify_album_alias_append_transition

PRODUCER = 'opengenius-typed-album-alias-append-chain-growth-rebinding-v1'
CHAIN_OPERATION = 'ordered_root_reviewed_album_alias_append_chain'

def _sha(data): return hashlib.sha256(data).hexdigest()

def _nonempty_relation_map(rows, label):
    relations = _relation_map(rows, label)
    return {pid: targets for pid, targets in relations.items() if targets}

def verify_reviewed_album_append_chain(*, track_snapshot, chain_receipt_path,
                                       start_map_path, final_map_path):
    """Replay each reviewed append step and bind the exact chain envelope."""
    chain_path, chain_raw, chain = _read(chain_receipt_path)
    start_path, start_raw, start_map = _read(start_map_path)
    final_path, final_raw, final_map = _read(final_map_path)
    snapshot_path, snapshot_raw, _ = _read(track_snapshot)
    if (chain.get('schema_version') != 1 or chain.get('operation') != CHAIN_OPERATION
            or chain.get('input_map_path') != str(start_path)
            or chain.get('input_map_sha256') != _sha(start_raw)
            or chain.get('output_map_path') != str(final_path)
            or chain.get('output_map_sha256') != _sha(final_raw)
            or chain.get('recording_edges_added') != 0
            or chain.get('recording_identity_verified') is not False
            or chain.get('active_map_changed') is not False):
        raise ValueError('Album append-chain receipt does not bind exact endpoints or metadata-only intent')
    steps = chain.get('steps')
    if not isinstance(steps, list) or len(steps) < 2:
        raise ValueError('Album append chain requires multiple separately reviewed steps')
    current_path = start_path
    current_map = start_map
    all_receipts = [_receipt(chain_path), _receipt(snapshot_path), _receipt(start_path)]
    transition_proofs = []
    seen_aliases = set()
    for i, step in enumerate(steps, 1):
        if (not isinstance(step, dict) or step.get('step') != i
                or step.get('verified_by_transition_replay') is not True
                or step.get('input_map_path') != str(current_path)
                or step.get('input_map_sha256') != _sha(current_path.read_bytes())):
            raise ValueError('Album append steps are skipped, reordered, or misbound')
        alias_mapping = step.get('alias_mapping')
        if (not isinstance(alias_mapping, dict)
                or set(alias_mapping) != {'remote_album_alias', 'canonical_library_album_target'}
                or not all(isinstance(v, str) and v for v in alias_mapping.values())
                or alias_mapping['remote_album_alias'] in seen_aliases):
            raise ValueError('Album append chain contains a malformed or repeated alias')
        seen_aliases.add(alias_mapping['remote_album_alias'])
        # Verify every declared receipt before relying on its path.
        field_map = {
            'proposal': ('proposal_path', 'proposal_sha256'),
            'source_evidence': ('source_evidence_path', 'source_evidence_sha256'),
            'candidate_audit': ('candidate_audit_path', 'candidate_audit_sha256'),
            'review_decisions': ('review_decisions_path', 'review_decisions_sha256'),
            'application_receipt': ('application_receipt_path', 'application_receipt_sha256'),
        }
        paths = {}
        for name, (path_key, hash_key) in field_map.items():
            if not isinstance(step.get(path_key), str) or not isinstance(step.get(hash_key), str):
                raise ValueError('Album append chain has a malformed ' + name + ' receipt')
            path = Path(step[path_key]).resolve()
            if _sha(path.read_bytes()) != step[hash_key]:
                raise ValueError('Album append chain ' + name + ' changed')
            paths[name] = path
            all_receipts.append(_receipt(path))
        output_path = Path(step.get('output_map_path', '')).resolve()
        if step.get('output_map_sha256') != _sha(output_path.read_bytes()):
            raise ValueError('Album append chain intermediate map changed')
        result = verify_album_alias_append_transition(
            track_snapshot=snapshot_path,
            identity_map_path=current_path,
            proposals_path=paths['proposal'],
            source_evidence_path=paths['source_evidence'],
            candidate_audit_path=paths['candidate_audit'],
            review_decisions_path=paths['review_decisions'],
            output_path=output_path,
            receipt_path=paths['application_receipt'])
        output_map = json.loads(output_path.read_bytes())
        aliases_before = current_map.get('album_title_aliases', [])
        aliases_after = output_map.get('album_title_aliases', [])
        if (result.get('input_identity_map_sha256') != _sha(current_path.read_bytes())
                or result.get('output_identity_map_sha256') != step['output_map_sha256']
                or result.get('alias_mapping') != alias_mapping
                or result.get('candidate_yield') != step.get('candidate_yield')
                or result.get('recording_identity_verified') is not False
                or result.get('recording_edges_added') != 0
                or len(aliases_after) != len(aliases_before) + 1
                or aliases_after[:-1] != aliases_before
                or aliases_after[-1].get('canonical_library_album') != alias_mapping['canonical_library_album_target']
                or aliases_after[-1].get('exact_remote_album_aliases') != [alias_mapping['remote_album_alias']]
                or {k:v for k,v in current_map.items() if k!='album_title_aliases'}
                   != {k:v for k,v in output_map.items() if k!='album_title_aliases'}):
            raise ValueError('Album append step is not an exact one-alias prefix extension')
        transition_proofs.append({
            'step': i, 'alias_mapping': alias_mapping,
            'input_identity_map': {'path': str(current_path), 'sha256': _sha(current_path.read_bytes())},
            'output_identity_map': {'path': str(output_path), 'sha256': _sha(output_path.read_bytes())},
            'proposal': _receipt(paths['proposal']), 'source_evidence': _receipt(paths['source_evidence']),
            'candidate_audit': _receipt(paths['candidate_audit']),
            'review_decisions': _receipt(paths['review_decisions']),
            'application_receipt': _receipt(paths['application_receipt']),
            'candidate_yield': result['candidate_yield'],
            'recording_identity_verified': False, 'recording_edges_added': 0,
        })
        current_path, current_map = output_path, output_map
    if current_path != final_path:
        raise ValueError('Album append chain does not terminate at its declared final map')
    all_receipts.append(_receipt(final_path))
    return {
        'chain_receipt': _receipt(chain_path),
        'track_snapshot': _receipt(snapshot_path),
        'input_identity_map': _receipt(start_path),
        'output_identity_map': _receipt(final_path),
        'transitions': transition_proofs,
        'proof_input_receipts': _proof_input_receipts(all_receipts),
        'recording_identity_verified': False, 'recording_edges_added': 0,
    }


def rebind_album_alias_append_chain_child(*, parent_graph_path, parent_report_path,
        parent_state_path, input_identity_map, output_identity_map, track_snapshot,
        chain_receipt_path, output_dir):
    """Bind a source-verified parent state through an ordered append chain."""
    output_dir=Path(output_dir).resolve()
    if output_dir.exists(): raise FileExistsError('Output directory exists; choose a new directory')
    graph_path, graph_bytes, graph=_read(parent_graph_path)
    report_path, report_bytes, report=_read(parent_report_path)
    state_path, state_bytes, state=_read(parent_state_path)
    binding=report.get('album_alias_chain_growth_rebinding') or {}
    parent_map=binding.get('output_identity_map') or {}
    start_path,start_bytes,start_map=_read(input_identity_map)
    final_path,final_bytes,final_map=_read(output_identity_map)
    snapshot_path,snapshot_bytes,_=_read(track_snapshot)
    if (report.get('producer')!='opengenius-typed-album-alias-chain-growth-rebinding-v1'
        or parent_map.get('path')!=str(start_path) or parent_map.get('sha256')!=_sha(start_bytes)
        or graph.get('source_state_sha256')!=_sha(state_bytes)
        or graph.get('source_identity_map_rebinding_report_sha256')!=_sha(report_bytes)):
        raise ValueError('Append child does not continue exact typed chain-rebind parent')
    policy=graph.get('source_policy')
    if (policy not in (None,'authenticated-watch-radio-only-v1')
        or ('source_policy' in report)!=(policy is not None)
        or ('source_policy' in report and report.get('source_policy')!=policy)):
        raise ValueError('Append child parent source policy is absent or inconsistent')
    parent_proof=verify_album_alias_chain_growth_rebinding_report(
        graph_doc=graph,report=report,state=state,library_hash=report.get('zero_id_library_sha256'),
        identity_hash=_sha(start_bytes),state_path=state_path)
    tracks,library_hash,_=load_library(track_snapshot=snapshot_path)
    if library_hash!=report.get('zero_id_library_sha256'):
        raise ValueError('Append child snapshot differs from source Library')
    append_proof=verify_reviewed_album_append_chain(track_snapshot=snapshot_path,
        chain_receipt_path=chain_receipt_path,start_map_path=start_path,final_map_path=final_path)
    parent_roots=graph.get('accepted_roots')
    _validate_state_graph_relation_projection(
        parent_roots, _state_graph_rows(state), 'Append-chain parent')
    child_state=_rebind_state_map_hash(state,_sha(final_bytes))
    final_matcher=IdentityMap(final_map,tracks,library_hash)
    if (state['rows']!=child_state['rows']
        or _relation_map(_state_graph_rows(child_state),'Child state')!=_relation_map(_state_graph_rows(state),'Parent state')
        or plan_genius_update(child_state,parent_roots,final_matcher,library_hash,_sha(final_bytes))['next_state']['state_sha256']!=child_state['state_sha256']):
        raise ValueError('Append child altered assignments/metadata/relations or failed state reproduction')
    output_dir.mkdir(parents=True)
    child_state_path=output_dir/'state.json'; child_report_path=output_dir/'identity-map-rebinding-report.json'; child_graph_path=output_dir/'accepted-baseline-graphs.json'
    write_json(child_state_path,child_state)
    transitive=_proof_input_receipts(parent_proof+append_proof['proof_input_receipts']+[
        _receipt(graph_path),_receipt(report_path),_receipt(state_path),_receipt(start_path),
        _receipt(final_path),_receipt(snapshot_path),_receipt(child_state_path)])
    child_binding={
        'schema_version':1,'parent_typed_growth':{
            'graph_export':_receipt(graph_path),'rebase_report':_receipt(report_path),'state':_receipt(state_path),
            'input_identity_map':binding['input_identity_map'],'output_identity_map':binding['output_identity_map']},
        **({'source_policy':policy} if policy is not None else {}),
        'track_snapshot':_receipt(snapshot_path),'input_identity_map':_receipt(start_path),
        'output_identity_map':_receipt(final_path),'append_chain':append_proof,
        'old_state_sha256':state['state_sha256'],'new_state_sha256':child_state['state_sha256'],
        'local_track_metadata_unchanged':True,'local_equivalence_labels_unchanged':True,
        'pid_gid_assignments_unchanged':True,'metadata_bytes_unchanged':True,'relation_bytes_unchanged':True,
        'old_growth_replayed_with_parent_map':True,'recording_identity_verified':False,
        'recording_edges_added_by_rebinding':0,'proof_input_receipts':transitive,
    }
    child_report={
        'schema_version':1,'producer':PRODUCER,'zero_id_library_sha256':library_hash,
        'identity_map_sha256':_sha(final_bytes),'next_state_sha256':child_state['state_sha256'],
        'indexed_tracks':len(state_rows(child_state)[0]),'source_state_path':str(child_state_path.resolve()),
        'album_alias_append_chain_growth_rebinding':child_binding,
        'relation_revalidation':deepcopy(report['relation_revalidation']),
        'raw_input_provenance':deepcopy(report['raw_input_provenance']),
        'accepted_root_count':report.get('accepted_root_count'),'directed_edge_count':report.get('directed_edge_count'),
        **({'source_policy':policy} if policy is not None else {}),
    }
    write_json(child_report_path,child_report)
    child_graph={'schema_version':1,'producer':'verified-identity-map-rebound-state-graph-export',
        'source_state_path':str(child_state_path.resolve()),'source_state_sha256':_sha(child_state_path.read_bytes()),
        'source_identity_map_rebinding_report_sha256':_sha(child_report_path.read_bytes()),
        'accepted_roots':parent_roots,'identity_status':'unverified_metadata_candidate'}
    if policy is not None: child_graph['source_policy']=policy
    write_json(child_graph_path,child_graph)
    return {'output_dir':str(output_dir),'report_path':str(child_report_path),'graph_path':str(child_graph_path),
        'state_path':str(child_state_path),'identity_map_sha256':_sha(final_bytes),
        'state_sha256':child_state['state_sha256'],'preserved_tracks':len(state_rows(child_state)[0]),
        'preserved_directed_edges':child_report['directed_edge_count'],'accepted_root_count':child_report['accepted_root_count'],
        'recording_identity_verified':False,'recording_edges_added':0}


def verify_album_alias_append_chain_growth_rebinding_report(*,graph_doc,report,state,library_hash,identity_hash,state_path):
    binding=report.get('album_alias_append_chain_growth_rebinding')
    if (report.get('producer')!=PRODUCER or not isinstance(binding,dict)
        or binding.get('recording_identity_verified') is not False
        or binding.get('recording_edges_added_by_rebinding')!=0
        or report.get('zero_id_library_sha256')!=library_hash or report.get('identity_map_sha256')!=identity_hash):
        raise ValueError('Album append child proof is incomplete')
    parent=binding.get('parent_typed_growth')
    if not isinstance(parent,dict) or set(parent)!={'graph_export','rebase_report','state','input_identity_map','output_identity_map'}:
        raise ValueError('Append child omits exact parent receipts')
    def checked(ref,label):
        if not isinstance(ref,dict) or set(ref)!={'path','sha256'}: raise ValueError('Malformed '+label)
        path,data,doc=_read(ref['path'])
        if _sha(data)!=ref['sha256']: raise ValueError(label+' changed')
        return path,data,doc
    pgpath,pgbytes,pg=checked(parent['graph_export'],'parent graph')
    prpath,prbytes,pr=checked(parent['rebase_report'],'parent report')
    pspath,psbytes,ps=checked(parent['state'],'parent state')
    parent_input_path,parent_input_bytes,_=checked(parent['input_identity_map'],'parent input map')
    inputpath,inputbytes,_=checked(parent['output_identity_map'],'parent output map')
    policy=pg.get('source_policy')
    if (policy not in (None,'authenticated-watch-radio-only-v1')
        or ('source_policy' in pr)!=(policy is not None)
        or ('source_policy' in pg)!=('source_policy' in pr)
        or ('source_policy' in binding)!=(policy is not None)
        or ('source_policy' in report)!=(policy is not None)
        or ('source_policy' in graph_doc)!=(policy is not None)
        or (policy is not None and any(d.get('source_policy')!=policy for d in (pr,binding,report,graph_doc)))
        or pr.get('producer')!='opengenius-typed-album-alias-chain-growth-rebinding-v1'
        or pg.get('source_identity_map_rebinding_report_sha256')!=_sha(prbytes)
        or pg.get('source_state_sha256')!=_sha(psbytes)
        or (pr.get('album_alias_chain_growth_rebinding') or {}).get('output_identity_map')!=parent['output_identity_map']):
        raise ValueError('Append child did not inherit exact parent policy/report/map')
    parent_receipts=verify_album_alias_chain_growth_rebinding_report(graph_doc=pg,report=pr,state=ps,
        library_hash=library_hash,identity_hash=_sha(inputbytes),state_path=pspath)
    if (parent_input_path!=Path((pr.get('album_alias_chain_growth_rebinding') or {}).get('input_identity_map',{}).get('path','')).resolve()
        or binding.get('input_identity_map')!=parent['output_identity_map']
        or inputpath!=Path(binding.get('input_identity_map',{}).get('path','')).resolve()):
        raise ValueError('Append child starts from another map')
    snapref=binding.get('track_snapshot'); outref=binding.get('output_identity_map')
    snap_path,snap_bytes,_=checked(snapref,'track snapshot'); out_path,out_bytes,outmap=checked(outref,'output map')
    if (binding.get('output_identity_map') != {'path':str(out_path),'sha256':_sha(out_bytes)}
        or report.get('identity_map_sha256')!=identity_hash
        or _sha(out_bytes)!=identity_hash or Path(report.get('source_state_path','')).resolve()!=Path(state_path).resolve()):
        raise ValueError('Append child endpoints differ from report')
    append=binding.get('append_chain')
    if not isinstance(append,dict): raise ValueError('Append child omitted append-chain replay receipt')
    reproduced=verify_reviewed_album_append_chain(track_snapshot=snap_path,
        chain_receipt_path=append.get('chain_receipt',{}).get('path'),
        start_map_path=inputpath,final_map_path=out_path)
    if reproduced!=append: raise ValueError('Album append chain differs from source replay')
    expected_state=_rebind_state_map_hash(ps,identity_hash)
    if state!=expected_state or binding.get('old_state_sha256')!=ps.get('state_sha256') or binding.get('new_state_sha256')!=state.get('state_sha256') or report.get('next_state_sha256')!=state.get('state_sha256'):
        raise ValueError('Append child changed state beyond map hash')
    roots=pg.get('accepted_roots')
    if graph_doc.get('accepted_roots') != roots:
        raise ValueError('Append child changed accepted roots or order')
    _validate_state_graph_relation_projection(
        roots, _state_graph_rows(state), 'Verified append child')
    tracks,lh,_=load_library(track_snapshot=snap_path)
    if lh!=library_hash or plan_genius_update(state,roots,IdentityMap(outmap,tracks,lh),lh,identity_hash)['next_state']['state_sha256']!=state['state_sha256']:
        raise ValueError('Append child final-map state reproduction failed')
    if (report.get('relation_revalidation')!=pr.get('relation_revalidation')
        or report.get('raw_input_provenance')!=pr.get('raw_input_provenance')):
        raise ValueError('Append child changed parent source audit')
    expected=_proof_input_receipts(parent_receipts+append['proof_input_receipts']+[
        _receipt(pgpath),_receipt(prpath),_receipt(pspath),_receipt(parent_input_path),_receipt(inputpath),
        _receipt(out_path),_receipt(snap_path),_receipt(Path(state_path).resolve())])
    saved=_canonical_receipt_rows(binding.get('proof_input_receipts'))
    if saved!=expected: raise ValueError('Append child omitted transitive input receipts')
    return saved
