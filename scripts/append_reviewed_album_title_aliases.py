"""Append exactly one root-reviewed whole-album metadata alias to a map."""
import copy, hashlib, json, tempfile
from pathlib import Path
from apply_reviewed_album_title_aliases import _candidate_pid_set, _sha
from music_identity_map import IdentityMap, load_library


def _read(path):
    p=Path(path).resolve(); raw=p.read_bytes(); return p,raw,json.loads(raw)

def _hash(path): return _sha(Path(path).read_bytes())

def _validate(*,track_snapshot,identity_map_path,proposals_path,source_evidence_path,
              candidate_audit_path,review_decisions_path):
    snapshot=Path(track_snapshot).resolve()
    map_path,map_raw,im=_read(identity_map_path)
    pp,praw,proposal=_read(proposals_path); ep,eraw,source=_read(source_evidence_path)
    ap,araw,audit=_read(candidate_audit_path); rp,rraw,review=_read(review_decisions_path)
    tracks,library_hash,_=load_library(track_snapshot=snapshot)
    map_hash=_sha(map_raw); snap_hash=_hash(snapshot)
    hashes={'proposal':_sha(praw),'source':_sha(eraw),'audit':_sha(araw),'review':_sha(rraw)}
    if proposal.get('schema_version')!=3 or proposal.get('status')!='needs_root_review' or proposal.get('proposed_identity_map_schema_field')!='album_title_aliases':
        raise ValueError('Proposal must use the existing reviewed album-alias schema')
    payload=proposal.get('proposal') or {}; alias=payload.get('remote_album_alias'); target=payload.get('canonical_library_album_target')
    if (payload.get('metadata_only') is not True or payload.get('recording_identity_verified') is not False
        or payload.get('new_recording_edges_authorized') is not False or not isinstance(alias,str) or not alias
        or not isinstance(target,str) or not target): raise ValueError('Proposal is not one metadata-only alias')
    if proposal.get('source_evidence')!={'path':str(ep),'sha256':hashes['source']} or proposal.get('cache_audit')!={'path':str(ap),'sha256':hashes['audit']}:
        raise ValueError('Proposal source and audit receipts mismatch')
    if (source.get('proposed_exact_remote_album_alias')!=alias or source.get('local_album')!=target
        or source.get('recording_identity_verified') is not False or source.get('album_edition_identity_verified') is not False
        or audit.get('source_evidence')!={'path':str(ep),'sha256':hashes['source']}): raise ValueError('Source evidence does not match exact proposal')
    if (audit.get('library',{}).get('track_snapshot_sha256')!=snap_hash or audit.get('library',{}).get('identity_map_sha256')!=map_hash
        or audit.get('library',{}).get('library_sha256')!=library_hash
        or audit.get('library',{}).get('local_album_pid_count')!=sum(t.get('album')==target for t in tracks)):
        raise ValueError('Candidate audit is bound to another input map or Library')
    approved=review.get('approved_mapping') or {}; approval=review.get('approval') or {}
    if (review.get('schema_version')!=1 or review.get('proposal_sha256')!=hashes['proposal']
        or review.get('source_evidence_sha256')!=hashes['source'] or review.get('candidate_audit_sha256')!=hashes['audit']
        or review.get('input_identity_map_sha256')!=map_hash or review.get('library_sha256')!=library_hash
        or approval.get('reviewed_by')!='root' or approval.get('metadata_equivalence_only') is not True
        or approval.get('recording_identity_verified') is not False or approval.get('new_recording_edges_authorized') is not False
        or approved!={'remote_album_alias':alias,'canonical_library_album_target':target}):
        raise ValueError('Root review does not approve this exact alias/input map')
    return locals()

def _build(ctx):
    im=ctx['im']; tracks=ctx['tracks']; library_hash=ctx['library_hash']
    payload=ctx['proposal']['proposal']; alias=payload['remote_album_alias']; target=payload['canonical_library_album_target']
    matcher=IdentityMap(im,tracks,library_hash)
    if alias in matcher.album_title_aliases or sum(t.get('album')==target for t in tracks)<1:
        raise ValueError('Alias collides or canonical target is absent')
    files=ctx['audit'].get('cache_scan',{}).get('files')
    if not isinstance(files,list) or not files: raise ValueError('Audit lacks hashed cache inputs')
    docs=[]
    for receipt in files:
        if not isinstance(receipt,dict) or not isinstance(receipt.get('path'),str): raise ValueError('Malformed cache receipt')
        p=Path(receipt['path']); p=p if p.is_absolute() else ctx['snapshot'].parents[3]/p; p=p.resolve()
        if not p.is_file() or _hash(p)!=receipt.get('sha256'): raise ValueError('Cached observation source changed')
        docs.append((p,json.loads(p.read_bytes())))
    qualifying=[]
    for p,doc in docs:
        for obs in doc.get('observations') or []:
            remote=obs.get('track') or {}
            if not obs.get('is_seed') and (remote.get('album') or {}).get('name')==alias:
                pids=_candidate_pid_set(matcher,remote,alias)
                if len(pids)==1: qualifying.append((p,obs,next(iter(pids))))
    pids={pid for _,_,pid in qualifying}; vids={obs.get('track',{}).get('video_id') for _,obs,_ in qualifying if obs.get('track',{}).get('video_id')}
    expected={r.get('pid') for r in payload.get('candidate_tracks') or []}; y=payload.get('candidate_yield') or {}
    if (pids!=expected or len(pids)!=y.get('distinct_local_pids_passing_exact_title_credit_duration_version_and_pre_album_uniqueness_gates_after_simulated_alias')
        or len(qualifying)!=y.get('eligible_cached_observations') or len(vids)!=y.get('eligible_unique_videos')
        or ctx['audit'].get('summary',{}).get('album_alias_only_candidate_yield_pid_count')!=len(pids)):
        raise ValueError(f'Exact candidate replay differs: pids={len(pids)}/{len(expected)} obs={len(qualifying)}/{y.get("eligible_cached_observations")} videos={len(vids)}/{y.get("eligible_unique_videos")}')
    result=copy.deepcopy(im); before=copy.deepcopy(im.get('album_title_aliases',[]))
    result['album_title_aliases']=before+[{'enabled':True,'canonical_library_album':target,
        'exact_remote_album_aliases':[alias],'status':'root_reviewed_metadata_alias','evidence':{
          'proposal_sha256':ctx['hashes']['proposal'],'source_evidence_sha256':ctx['hashes']['source'],
          'candidate_audit_sha256':ctx['hashes']['audit'],'review_decisions_sha256':ctx['hashes']['review'],
          'input_identity_map_sha256':ctx['map_hash']}}]
    resultmatcher=IdentityMap(result,tracks,library_hash)
    for _,obs,pid in qualifying:
        remote=obs.get('track') or {}; beforematch,=matcher.match(remote); after,=resultmatcher.match(remote)
        if beforematch!={**after,'album_match':False} or after.get('persistent_id')!=pid or after.get('album_match') is not True:
            raise ValueError('Append altered non-album matching fields')
    if (result.get('recording_links')!=im.get('recording_links') or result.get('tracks')!=im.get('tracks')):
        raise ValueError('Append changed recording links or canonical track fields')
    return result,pids,len(qualifying),len(vids)

def apply_reviewed_album_title_alias_append(*,track_snapshot,identity_map_path,proposals_path,
 source_evidence_path,candidate_audit_path,review_decisions_path,output_path,receipt_path):
    ctx=_validate(track_snapshot=track_snapshot,identity_map_path=identity_map_path,proposals_path=proposals_path,
       source_evidence_path=source_evidence_path,candidate_audit_path=candidate_audit_path,review_decisions_path=review_decisions_path)
    out=Path(output_path).resolve(); rec=Path(receipt_path).resolve()
    inputs={ctx['snapshot'],ctx['map_path'],ctx['pp'],ctx['ep'],ctx['ap'],ctx['rp']}
    if out.exists() or rec.exists() or out==rec or out in inputs or rec in inputs: raise FileExistsError('Outputs must be new distinct files')
    result,pids,obs_count,vid_count=_build(ctx); out.parent.mkdir(parents=True,exist_ok=True); rec.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2); f.write('\n')
    out_hash=_hash(out); receipt={'schema_version':1,'operation':'append_root_reviewed_exact_whole_album_metadata_alias',
      'track_snapshot_path':str(ctx['snapshot']),'track_snapshot_sha256':ctx['snap_hash'],'library_sha256':ctx['library_hash'],
      'input_identity_map_path':str(ctx['map_path']),'input_identity_map_sha256':ctx['map_hash'],
      'output_identity_map_path':str(out),'output_identity_map_sha256':out_hash,
      'proposals_path':str(ctx['pp']),'proposal_sha256':ctx['hashes']['proposal'],
      'source_evidence_path':str(ctx['ep']),'source_evidence_sha256':ctx['hashes']['source'],
      'candidate_audit_path':str(ctx['ap']),'candidate_audit_sha256':ctx['hashes']['audit'],
      'review_decisions_path':str(ctx['rp']),'review_decisions_sha256':ctx['hashes']['review'],
      'alias_mapping':{'remote_album_alias':ctx['proposal']['proposal']['remote_album_alias'],'canonical_library_album_target':ctx['proposal']['proposal']['canonical_library_album_target']},
      'existing_alias_prefix_count':len(ctx['im'].get('album_title_aliases',[])),
      'candidate_yield':{'distinct_pids':len(pids),'eligible_observations':obs_count,'eligible_unique_videos':vid_count},
      'canonical_library_fields_changed':False,'recording_identity_verified':False,'recording_edges_added':0}
    with rec.open('x',encoding='utf-8') as f: json.dump(receipt,f,ensure_ascii=False,indent=2); f.write('\n')
    return receipt

def verify_album_alias_append_transition(*,track_snapshot,identity_map_path,proposals_path,source_evidence_path,
 candidate_audit_path,review_decisions_path,output_path,receipt_path):
    out=Path(output_path).resolve(); rec=Path(receipt_path).resolve(); saved=json.loads(rec.read_bytes())
    with tempfile.TemporaryDirectory(prefix='opengenius-album-alias-append-') as d:
        d=Path(d); rm=d/'map.json'; rr=d/'receipt.json'
        rebuilt=apply_reviewed_album_title_alias_append(track_snapshot=track_snapshot,identity_map_path=identity_map_path,
         proposals_path=proposals_path,source_evidence_path=source_evidence_path,candidate_audit_path=candidate_audit_path,
         review_decisions_path=review_decisions_path,output_path=rm,receipt_path=rr)
        if _hash(out)!=rebuilt['output_identity_map_sha256']: raise ValueError('Appended map differs from replay')
        saved.pop('output_identity_map_path',None); rebuilt.pop('output_identity_map_path',None)
        if saved!=rebuilt: raise ValueError('Append receipt differs from replay')
    return saved
