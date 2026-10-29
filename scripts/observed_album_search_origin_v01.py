"""Validate album-ID hints from the sealed authenticated first-page probe.

An album ID is discovery metadata only. Track identity still requires the
ordinary catalog title, complete credits, version, duration and uniqueness gates.
"""
import hashlib
import json
from pathlib import Path

KIND = 'observed_authenticated_album_first_page_v1'
SUMMARY = Path('/tmp/opengenius-official-album-discovery-v03-private/summary.json')
SUMMARY_SHA256 = 'b87b0adeb6d89f59e3b95a6a2976c49f6cefd037f9ffacca38cbee67242278fd'
KEYS = {'source_kind', 'summary_path', 'summary_sha256', 'response_path',
        'response_sha256', 'query_ordinal', 'result_position'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(source, album_id, album_title):
    from music_identity_map import normalize
    if (not isinstance(source, dict) or set(source) != KEYS
            or source.get('source_kind') != KIND
            or source.get('summary_path') != str(SUMMARY)
            or source.get('summary_sha256') != SUMMARY_SHA256
            or sha(SUMMARY) != SUMMARY_SHA256):
        raise ValueError('Observed album discovery summary differs from its sealed receipt')
    summary = json.loads(SUMMARY.read_text())
    ordinal = source.get('query_ordinal')
    position = source.get('result_position')
    if (type(ordinal) is not int or ordinal not in (0, 3, 4)
            or type(position) is not int or position < 0
            or summary.get('account_authentication_verified') is not True
            or summary.get('status') != 'bounded_diagnostic_completed'
            or summary.get('http_requests') != 8
            or summary.get('attempted_pids') != 0
            or summary.get('outgoing_roots_created') != 0
            or summary.get('recommendation_edges_created') != 0):
        raise ValueError('Observed album discovery has an unsupported query or scope')
    path = SUMMARY.parent / 'first-pages' / f'{ordinal:02d}-first-page.json'
    title = summary['titles'][ordinal]
    if (source.get('response_path') != str(path)
            or source.get('response_sha256') != title['first_page_receipt_sha256']
            or sha(path) != source['response_sha256']):
        raise ValueError('Observed album first-page receipt changed')
    page = json.loads(path.read_text())
    response = page.get('response', {})
    if (page.get('producer') != 'opengenius-official-album-search-first-page-v03'
            or page.get('query_ordinal') != ordinal
            or page.get('intent_sha256') != summary.get('intent_sha256')
            or page.get('local_album_title') != title['local_album_title']
            or response.get('method') != 'POST'
            or response.get('endpoint_path_without_query') != '/youtubei/v1/search'
            or response.get('safe_category') != 'search_page'
            or response.get('status_code') != 200):
        raise ValueError('Observed album first-page transport or query differs')
    candidates = response.get('sanitized_search_json', {}).get('album_candidates', [])
    if position >= len(candidates):
        raise ValueError('Observed album position does not exist')
    result = candidates[position]
    matching = [r for r in candidates if r.get('browseId') == album_id
                and normalize(r.get('title', '')) == normalize(album_title)]
    if (len(matching) != 1 or result != matching[0]
            or normalize(album_title) != normalize(title['local_album_title'])):
        raise ValueError('Observed album ID/title is not its unique exact first-page result')
    return result
