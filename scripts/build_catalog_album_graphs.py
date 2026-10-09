"""Build typed same-album catalog relations beside accepted radio relations.

Catalog membership is saved metadata evidence, not a YTMusic radio observation.
Every catalog edge points back to an actual get_album response and its album
plan parent; no network access occurs here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from discover_album_catalog_seeds import (build_album_catalog_evidence,
                                          validate_album_plan)
from genius_format import parse_similarities
from music_identity_map import (IdentityMap, load_library, normalize,
                                version_facets, version_signature)
from plan_genius_update import _source, plan_genius_update
from seed_video_evidence import validate_album_catalog_evidence
from update_genius_bundle import state_rows

PRODUCER = 'opengenius-catalog-album-graphs-v1'
VIDEO_RE = __import__('re').compile(r'[A-Za-z0-9_-]{11}\Z')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return _sha(raw)


def _read_json(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return json.loads(raw), raw, path


def _baseline_graphs(state, accepted_graphs):
    ids, _, relations = state_rows(state)
    reverse = {gid: pid for pid, gid in ids.items()}
    if len(reverse) != len(ids):
        raise ValueError('Baseline state has duplicate Genius IDs')
    edges = {pid: [reverse[gid] for gid in parse_similarities(relations[ids[pid]])[1]]
             for pid in ids}
    roots = accepted_graphs.get('accepted_roots')
    if not isinstance(roots, list):
        roots = accepted_graphs.get('relation_rows')
    if not isinstance(roots, list):
        raise ValueError('Baseline accepted graph lacks root rows')
    by_root = {}
    for row in roots:
        if (not isinstance(row, dict) or not isinstance(row.get('root_pid'), str)
                or not isinstance(row.get('ordered_target_pids'), list)
                or row['root_pid'] not in ids or row['root_pid'] in by_root):
            raise ValueError('Malformed baseline accepted graph root')
        targets = row['ordered_target_pids']
        if (any(not isinstance(pid, str) or pid not in ids or pid == row['root_pid']
                for pid in targets) or len(set(targets)) != len(targets)):
            raise ValueError('Invalid baseline accepted graph target')
        by_root[row['root_pid']] = row
    for root, targets in edges.items():
        record = by_root.get(root)
        if targets:
            if record is None or record['ordered_target_pids'] != targets:
                raise ValueError('Accepted radio graph differs from baseline state edges')
        elif record is not None and record['ordered_target_pids']:
            raise ValueError('Accepted radio graph differs from baseline state edges')
    for root, row in by_root.items():
        if row['ordered_target_pids'] != edges[root]:
            raise ValueError('Accepted radio graph differs from baseline state edges')
    return ids, edges, by_root


def _validated_responses(plan, evidence, evidence_path, tracks, matcher,
                         library_hash, identity_map_hash, plan_sha256):
    if evidence.get('album_plan_sha256') != plan_sha256:
        raise ValueError('Catalog evidence does not bind the supplied album plan')
    validate_album_catalog_evidence(evidence, evidence_path, tracks, matcher,
                                    library_hash, identity_map_hash)
    plan_rows = {row['album_id']: row for row in plan['albums']}
    requests = {}
    for observation in evidence.get('catalog_observations', []):
        if not isinstance(observation, dict):
            raise ValueError('Malformed catalog observation')
        album_id = observation.get('album_id')
        source = observation.get('source_request')
        if (album_id not in plan_rows or not isinstance(source, dict)
                or source.get('endpoint') != 'get_album'
                or source.get('argument_key') != 'browseId'
                or source.get('argument_value') != album_id
                or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)):
            raise ValueError('Catalog response lacks a saved plan parent')
        item = (source['path'], source['sha256'])
        if album_id in requests and requests[album_id] != item:
            raise ValueError('Album ID maps to multiple response files')
        requests[album_id] = item
    responses = []
    for album_id, (raw_path, expected_sha) in sorted(requests.items()):
        path = Path(raw_path).resolve()
        raw = path.read_bytes()
        if _sha(raw) != expected_sha:
            raise ValueError('Catalog response hash changed')
        document = json.loads(raw)
        if (not isinstance(document, dict) or document.get('endpoint') != 'get_album'
                or document.get('arguments') != {'browseId': album_id}
                or set(document) != {'endpoint', 'arguments', 'observed_at', 'response'}
                or not isinstance(document.get('observed_at'), str)
                or not isinstance(document.get('response'), dict)):
            raise ValueError('Catalog response does not match its saved browse ID')
        planned = plan_rows[album_id]
        title = document['response'].get('title')
        if (not isinstance(title, str) or normalize(title) != normalize(planned.get('album_name') or '')
                or not isinstance(document['response'].get('tracks'), list)):
            raise ValueError('Catalog response album differs from its validated plan')
        responses.append({'album_id': album_id, 'response': document['response'],
                          'path': str(path), 'sha256': expected_sha})
    rebuilt = build_album_catalog_evidence(plan, responses, tracks, matcher,
                                           library_hash, identity_map_hash)
    # The saved evidence and independently regenerated rows must agree exactly.
    if rebuilt['catalog_observations'] != evidence.get('catalog_observations'):
        raise ValueError('Catalog observations differ from the actual saved responses')
    return responses, rebuilt


def _catalog_members(responses, plan, tracks, matcher, evidence_observations):
    by_pid = {track['persistent_id']: track for track in tracks}
    plan_by_id = {row['album_id']: row for row in plan['albums']}
    observed_index = {}
    for row in evidence_observations:
        observed_index[(row['album_id'], row['position'])] = row
    video_pids = {}
    matches = []
    skipped = {'invalid_video_or_track': 0, 'nonunique_or_incomplete_match': 0,
               'album_or_version_mismatch': 0}
    for response in responses:
        album_id, response_doc = response['album_id'], response['response']
        album_name = response_doc['title']
        planned = plan_by_id[album_id]
        for position, remote in enumerate(response_doc['tracks'], 1):
            if not isinstance(remote, dict) or not isinstance(remote.get('videoId'), str) or not VIDEO_RE.fullmatch(remote['videoId']):
                skipped['invalid_video_or_track'] += 1
                continue
            evidence_row = observed_index.get((album_id, position))
            # The existing evidence generator emits a typed row for each source
            # track with a syntactically valid video ID, even with no candidates.
            if evidence_row is None:
                skipped['invalid_video_or_track'] += 1
                continue
            remote_title = remote.get('title')
            artists = remote.get('artists')
            if not isinstance(remote_title, str) or not remote_title.strip() or not isinstance(artists, list):
                skipped['invalid_video_or_track'] += 1
                continue
            remote_album = remote.get('album')
            if isinstance(remote_album, dict):
                remote_album = remote_album.get('name')
            if remote_album is not None and (not isinstance(remote_album, str)
                                             or normalize(remote_album) != normalize(album_name)):
                skipped['album_or_version_mismatch'] += 1
                continue
            match_row = dict(remote)
            match_row['album'] = {'name': album_name}
            candidates = matcher.match(match_row)
            if (len(candidates) != 1 or candidates[0].get('artist_credit_match') != 'complete'):
                skipped['nonunique_or_incomplete_match'] += 1
                continue
            candidate = candidates[0]
            pid = candidate['persistent_id']
            local = by_pid.get(pid)
            if local is None:
                skipped['nonunique_or_incomplete_match'] += 1
                continue
            local_album = local.get('album') or ''
            if (normalize(local_album) != normalize(album_name)
                    or normalize(candidate.get('local_metadata', {}).get('album') or '') != normalize(local_album)
                    or version_signature(local.get('title') or '') != version_signature(remote_title)):
                skipped['album_or_version_mismatch'] += 1
                continue
            credit = tuple(sorted(matcher.artist_credits(local.get('artist') or '')))
            if not credit:
                skipped['nonunique_or_incomplete_match'] += 1
                continue
            group_key = (album_id, credit, normalize(local_album))
            source = {'album_id': album_id, 'album_name': album_name,
                      'response_path': response['path'], 'response_sha256': response['sha256'],
                      'track_position': position, 'video_id': remote['videoId'],
                      'persistent_id': pid, 'source_plan_inputs': planned['source_inputs']}
            matches.append({'pid': pid, 'position': position, 'group_key': group_key,
                            'source': source, 'version_signature': sorted(version_signature(remote_title))})
            video_pids.setdefault(remote['videoId'], set()).add(pid)
    ambiguous_videos = {video for video, pids in video_pids.items() if len(pids) > 1}
    matches = [row for row in matches if row['source']['video_id'] not in ambiguous_videos]
    groups = {}
    for row in matches:
        key = row['group_key']
        members = groups.setdefault(key, {})
        old = members.get(row['pid'])
        if old is None or (row['position'], row['source']['video_id']) < (old['position'], old['source']['video_id']):
            members[row['pid']] = row
    return groups, ambiguous_videos, skipped, len(matches)


def _catalog_edges(groups, max_targets_per_root):
    raw_sources = {}
    for (album_id, credits, local_album), members in groups.items():
        ordered = sorted(members.values(), key=lambda row:(row['position'], row['pid']))
        for root in ordered:
            for target in ordered:
                if target['pid'] == root['pid']:
                    continue
                key = (root['pid'], target['pid'])
                source = {'source_relation': 'catalog_index', 'album_id': album_id,
                          'local_album_label': local_album,
                          'canonical_artist_credits': [list(item) for item in credits],
                          'root_track_position': root['position'],
                          'root_video_id': root['source']['video_id'],
                          'root_response_path': root['source']['response_path'],
                          'root_response_sha256': root['source']['response_sha256'],
                          'target_track_position': target['position'],
                          'target_video_id': target['source']['video_id'],
                          'target_response_path': target['source']['response_path'],
                          'target_response_sha256': target['source']['response_sha256'],
                          'source_plan_inputs': target['source']['source_plan_inputs']}
                raw_sources.setdefault(key, []).append(source)
    by_root = {}
    edge_sources = {}
    for (root, target), sources in raw_sources.items():
        sources.sort(key=lambda x:(x['album_id'], x['target_track_position'], x['target_video_id']))
        best = min(sources, key=lambda x:(abs(x['target_track_position']-x['root_track_position']),
                                          x['target_track_position'], x['album_id'], x['target_video_id']))
        by_root.setdefault(root, {})[target] = best
        edge_sources[(root,target)] = sources
    limited, limits = {}, {}
    for root, candidates in by_root.items():
        ranked = sorted(candidates.items(), key=lambda pair:(
            abs(pair[1]['target_track_position']-pair[1]['root_track_position']),
            pair[1]['target_track_position'], pair[1]['album_id'], pair[0]))
        if max_targets_per_root is not None:
            ranked = ranked[:max_targets_per_root]
        chosen = sorted(ranked, key=lambda pair:(pair[1]['target_track_position'],
                                                  pair[1]['album_id'], pair[0]))
        limited[root] = [pid for pid, _ in chosen]
        limits[root] = {pid for pid, _ in chosen}
    selected_sources = {key:value for key,value in edge_sources.items() if key[1] in limits.get(key[0], set())}
    return limited, selected_sources


def _local_catalog_edges(tracks, matcher, library_hash, snapshot_sha256,
                        max_targets_per_root):
    """Build typed metadata-only neighbors from the exact local Library rows."""
    groups = {}
    by_pid = {}
    for track in tracks:
        pid = track.get('persistent_id')
        album = normalize(track.get('album') or '')
        artist = track.get('artist')
        title = track.get('title') or ''
        duration_ms = track.get('duration_ms')
        # artist_credits intentionally has an empty-literal sentinel for some
        # other matching flows; local catalog membership requires real source
        # credits and a usable recording duration.
        if (not isinstance(artist, str) or not artist.strip()
                or not isinstance(title, str) or not title.strip()
                or type(duration_ms) is not int or duration_ms <= 0):
            continue
        credits = tuple(sorted(matcher.artist_credits(artist)))
        # Equal signatures keep explicitly distinguished versions (BGM, vocal,
        # off-vocal, karaoke, live, etc.) in separate groups.
        signature_flags = set(version_signature(title))
        normalized_title = normalize(title).replace('_', ' ')
        # Preserve common instrumental/vocal arrangement labels not currently
        # part of the shared alias matcher facets.
        if re.search(r'\bbgm\b|ＢＧＭ', normalized_title, re.IGNORECASE):
            signature_flags.add('bgm')
        if re.search(r'\bvocal\b|ボーカル', normalized_title, re.IGNORECASE):
            signature_flags.add('vocal')
        signature = tuple(sorted(signature_flags))
        facets = version_facets(title)
        # original_title is the track identity itself, not a version facet;
        # every other facet (markers, years, kinds, unknown qualifiers) must
        # match exactly to keep arrangement/edition variants apart.
        facet_profile = json.dumps({key: facets[key] for key in
            ('markers', 'years', 'kinds', 'unknown_qualifiers')},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if not isinstance(pid, str) or not album or not credits:
            continue
        record_sha = _json_sha(track)
        by_pid[pid] = track
        groups.setdefault((album, credits, signature, facet_profile), []).append(
            {'pid': pid, 'title': title, 'album': track.get('album'),
             'position': 0, 'record_sha256': record_sha})
    edges, sources = {}, {}
    for (album, credits, signature, facet_profile), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda row: (normalize(row['title']), row['pid']))
        for root in members:
            candidates = [target for target in members if target['pid'] != root['pid']]
            ranked = sorted(candidates, key=lambda row: (normalize(row['title']), row['pid']))
            if max_targets_per_root is not None:
                ranked = ranked[:max_targets_per_root]
            edges[root['pid']] = [row['pid'] for row in ranked]
            for target in ranked:
                sources[(root['pid'], target['pid'])] = [{
                    'source_relation': 'local_album_catalog',
                    'source_library_sha256': library_hash,
                    'source_track_snapshot_sha256': snapshot_sha256,
                    'root_track_record_sha256': root['record_sha256'],
                    'target_track_record_sha256': target['record_sha256'],
                    'local_album_label': album,
                    'canonical_artist_credits': [list(item) for item in credits],
                    'root_version_signature': list(signature),
                    'target_version_signature': list(signature),
                    'version_facet_profile': json.loads(facet_profile),
                }]
    return edges, sources, sum(1 for members in groups.values() if len(members) > 1)


def _radio_sources(root, record, accepted_path, accepted_sha):
    inputs = record.get('snapshot_inputs') if isinstance(record, dict) else None
    return {'source_relation': 'radio', 'root_pid': root,
            'accepted_graphs_path': str(accepted_path), 'accepted_graphs_sha256': accepted_sha,
            'accepted_root_record_sha256': _json_sha(record) if record is not None else None,
            'raw_snapshot_inputs': inputs if isinstance(inputs, list) else []}


def build_catalog_album_graphs(*, album_plan, album_plan_path, catalog_evidence,
                               catalog_evidence_path, baseline_accepted_graphs,
                               baseline_accepted_graphs_path, baseline_state,
                               baseline_state_path, tracks, matcher, library_hash,
                               identity_map_hash, max_targets_per_root=100,
                               local_catalog=False, track_snapshot_sha256=None):
    if max_targets_per_root is not None and (type(max_targets_per_root) is not int or not 1 <= max_targets_per_root <= 100):
        raise ValueError('Catalog target cap must be 1..100 or None')
    validate_album_plan(album_plan, library_hash, identity_map_hash)
    plan_path, evidence_path = Path(album_plan_path).resolve(), Path(catalog_evidence_path).resolve()
    plan_bytes, evidence_bytes = plan_path.read_bytes(), evidence_path.read_bytes()
    plan_sha, evidence_sha = _sha(plan_bytes), _sha(evidence_bytes)
    evidence_plan, evidence_raw, _ = _read_json(evidence_path)
    if evidence_plan != catalog_evidence:
        raise ValueError('Catalog evidence document differs from its source file')
    accepted_path, state_path = Path(baseline_accepted_graphs_path).resolve(), Path(baseline_state_path).resolve()
    accepted_sha, state_sha = _sha(accepted_path.read_bytes()), _sha(state_path.read_bytes())
    if baseline_state.get('state_sha256') != _sha(json.dumps({k:v for k,v in baseline_state.items() if k!='state_sha256'}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()):
        raise ValueError('Baseline state integrity mismatch')
    source, _ = _source(matcher, library_hash, identity_map_hash)
    if baseline_state.get('source') != source:
        raise ValueError('Baseline state source hashes differ from current Library and identity map')
    ids, baseline_edges, baseline_roots = _baseline_graphs(baseline_state, baseline_accepted_graphs)
    responses, rebuilt = _validated_responses(album_plan, catalog_evidence, evidence_path,
        tracks, matcher, library_hash, identity_map_hash, plan_sha)
    if _sha(evidence_raw) != evidence_sha:
        raise ValueError('Catalog evidence changed during validation')
    groups, ambiguous_videos, skipped, mapped_track_count = _catalog_members(
        responses, album_plan, tracks, matcher, rebuilt['catalog_observations'])
    catalog_edges, catalog_sources = _catalog_edges(groups, max_targets_per_root)
    local_edges, local_sources, local_group_count = ({}, {}, 0)
    if local_catalog:
        local_edges, local_sources, local_group_count = _local_catalog_edges(
            tracks, matcher, library_hash, track_snapshot_sha256,
            max_targets_per_root)
    full_rows=[]; edge_provenance=[]; root_provenance=[]
    old_edge_count=0; appended_count=0; catalog_only_edge_count=0; local_appended_count=0
    graph_roots = set(ids) | set(catalog_edges) | set(local_edges)
    for root in sorted(graph_roots):
        old=list(baseline_edges.get(root, [])); seen=set(old); merged=list(old)
        old_edge_count += len(old)
        record=baseline_roots.get(root)
        radio_source=_radio_sources(root,record,accepted_path,accepted_sha)
        catalog_targets=catalog_edges.get(root,[])
        for target in catalog_targets:
            if target not in seen:
                merged.append(target);seen.add(target);appended_count+=1;catalog_only_edge_count+=1
        local_targets=local_edges.get(root,[])
        for target in local_targets:
            if target not in seen:
                merged.append(target);seen.add(target);appended_count+=1;local_appended_count+=1
        full_rows.append({'root_pid':root,'ordered_target_pids':merged})
        if record is not None or catalog_targets or local_targets:
            root_provenance.append({'root_pid':root,'sources':(
                [radio_source] if record is not None else []) +
                ([{'source_relation':'catalog_index','catalog_album_ids':sorted({
                    source['album_id'] for target in catalog_targets
                    for source in catalog_sources.get((root,target),[])})}] if catalog_targets else []) +
                ([{'source_relation':'local_album_catalog','local_album_labels':sorted({
                    source['local_album_label'] for target in local_targets
                    for source in local_sources.get((root,target),[])})}] if local_targets else [])})
        for target in old:
            edge_provenance.append({'root_pid':root,'target_pid':target,'source_relation':'radio',
                'source':{**radio_source,'baseline_state_sha256':baseline_state['state_sha256']}})
        for target in catalog_targets:
            edge_provenance.append({'root_pid':root,'target_pid':target,'source_relation':'catalog_index',
                'already_present_as_radio_edge':target in set(old),
                'sources':catalog_sources.get((root,target),[])})
        for target in local_targets:
            edge_provenance.append({'root_pid':root,'target_pid':target,
                'source_relation':'local_album_catalog',
                'already_present_as_radio_edge':target in set(old),
                'sources':local_sources.get((root,target),[])})
    update=plan_genius_update(baseline_state,full_rows,matcher,library_hash,identity_map_hash)
    next_state=update['next_state']
    relation_rows=[{'root_pid':row['root_pid'],'ordered_target_pids':list(row['ordered_target_pids'])} for row in full_rows]
    common={'schema_version':1,'producer':PRODUCER,'library_sha256':library_hash,
            'identity_map_sha256':identity_map_hash,'baseline_state_sha256':baseline_state['state_sha256'],
            'baseline_state_path':str(state_path),'baseline_state_file_sha256':state_sha,
            'baseline_accepted_graphs_path':str(accepted_path),'baseline_accepted_graphs_sha256':accepted_sha,
            'album_plan_path':str(plan_path),'album_plan_sha256':plan_sha,
            'catalog_evidence_path':str(evidence_path),'catalog_evidence_sha256':evidence_sha,
            'catalog_response_inputs':[{'album_id':row['album_id'],'path':row['path'],'sha256':row['sha256']}
                                       for row in responses],
            'local_catalog_source':({'source_library_sha256':library_hash,
                'source_track_snapshot_sha256':track_snapshot_sha256,
                'record_hash_algorithm':'sha256(canonical JSON of exact local track record)',
                'identity_status':'unverified_metadata_candidate'} if local_catalog else None),
            'max_targets_per_root':max_targets_per_root,
            'source_relation_policy':{'radio':'accepted baseline edges retained in exact order',
                                      'catalog_index':'append-only same actual album ID, canonical artist credit set, and local album label; no radio observations fabricated',
                                      'local_album_catalog':'optional metadata-only local Library relation; source record hashes retained; not a remote observation'},
            'edge_provenance':edge_provenance,'root_provenance':root_provenance}
    accepted={'schema_version':1,'library_sha256':library_hash,
              'baseline_state_sha256':baseline_state['state_sha256'],
              'accepted_roots':relation_rows,
              'new_graphs':[{'root_pid':root,'ordered_target_pids':targets,'source_relation':'catalog_index'}
                            for root,targets in sorted(catalog_edges.items()) if targets],
              'local_album_catalog_graphs':[{'root_pid':root,'ordered_target_pids':targets,
                                              'source_relation':'local_album_catalog'}
                                             for root,targets in sorted(local_edges.items()) if targets],
              'skipped_snapshots':[],'snapshot_inputs':[],
              'catalog_source':{'producer':PRODUCER,'catalog_evidence_sha256':evidence_sha,
                                'album_plan_sha256':plan_sha,'response_inputs':common['catalog_response_inputs']}}
    relations={'schema_version':1,'source_library_sha256':library_hash,
               'source_state_sha256':next_state['state_sha256'],'relation_rows':relation_rows,
               'source_types':{'radio':'baseline accepted radio graph','catalog_index':'typed actual album catalog adjacency'}}
    evaluation={'schema_version':1,'library_sha256':library_hash,'relation_rows':relation_rows,
                'network_requests':0,'virtual_state_sha256':next_state['state_sha256'],
                'evaluation_scope':'accepted relation graph; catalog roots are not core-evaluated',
                'catalog_graph_producer':PRODUCER,'catalog_evidence_sha256':evidence_sha,
                'album_plan_sha256':plan_sha}
    return {'accepted_graphs':accepted,'relations':relations,'accepted_evaluation':evaluation,
            'state':next_state,'update_plan':update,'provenance':common,
            'audit':{'baseline_root_count':len(ids),'baseline_radio_edge_count':old_edge_count,
                     'catalog_group_count':len(groups),'catalog_matched_local_track_count':mapped_track_count,
                     'catalog_video_id_ambiguity_count':len(ambiguous_videos),
                     'catalog_root_count':len(catalog_edges),'catalog_edge_candidate_count':sum(map(len,catalog_edges.values())),
                     'catalog_edges_appended_count':catalog_only_edge_count,
                     'local_catalog_edges_appended_count':local_appended_count,
                     'total_edges_appended_count':appended_count,
                     'catalog_edges_already_present_as_radio_count':sum(
                         1 for row in edge_provenance if row['source_relation']=='catalog_index' and row['already_present_as_radio_edge']),
              'catalog_edge_limit_per_root':max_targets_per_root,'catalog_ambiguous_video_ids':sorted(ambiguous_videos),
                     'local_catalog_enabled':bool(local_catalog),'local_catalog_group_count':local_group_count,
                     'local_catalog_edge_candidate_count':sum(map(len,local_edges.values())),
                     'catalog_skipped_match_counts':skipped,'new_pids':update['new_pids'],
                     'new_pid_count':len(update['new_pids']),'next_state_row_count':len(next_state['rows']),
                     'radio_edges_retained_first':True,'recording_identity':'unverified_metadata_candidate',
                     'network_requests':0,'radio_observations_created':0,'recommendation_edges_created_from_catalog':0}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('track-snapshot','identity-map','baseline-state','baseline-accepted-graphs','album-plan','catalog-evidence','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--max-targets-per-root',type=int,default=100)
    parser.add_argument('--local-catalog',action='store_true',help='also add same-local-album, complete-credit, same-version-signature typed metadata relations')
    args=parser.parse_args()
    tracks,library_hash,_=load_library(track_snapshot=args.track_snapshot)
    map_bytes=args.identity_map.read_bytes(); identity_hash=_sha(map_bytes)
    mapping=json.loads(map_bytes); matcher=IdentityMap(mapping,tracks,library_hash)
    plan=json.loads(args.album_plan.read_text()); evidence=json.loads(args.catalog_evidence.read_text())
    state=json.loads(args.baseline_state.read_text()); accepted=json.loads(args.baseline_accepted_graphs.read_text())
    result=build_catalog_album_graphs(album_plan=plan,album_plan_path=args.album_plan,
        catalog_evidence=evidence,catalog_evidence_path=args.catalog_evidence,
        baseline_accepted_graphs=accepted,baseline_accepted_graphs_path=args.baseline_accepted_graphs,
        baseline_state=state,baseline_state_path=args.baseline_state,tracks=tracks,matcher=matcher,
        library_hash=library_hash,identity_map_hash=identity_hash,
        max_targets_per_root=args.max_targets_per_root,local_catalog=args.local_catalog,
        track_snapshot_sha256=hashlib.sha256(args.track_snapshot.read_bytes()).hexdigest())
    out=Path(args.output).resolve()
    if out.exists():parser.error('Output directory exists')
    out.mkdir(parents=True)
    for filename,key in [('accepted-graphs.json','accepted_graphs'),('relations.json','relations'),
                         ('accepted-evaluation.json','accepted_evaluation'),('state.json','state'),
                         ('catalog-provenance.json','provenance'),('catalog-update-plan.json','update_plan'),
                         ('summary.json','audit')]:
        (out/filename).write_text(json.dumps(result[key],ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['audit'],ensure_ascii=False))

if __name__=='__main__':
    main()
