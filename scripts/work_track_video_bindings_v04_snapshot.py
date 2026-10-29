"""Review-only bindings between an official work tracklist and video metadata.

This module never verifies recording identity and never writes an identity map.
Its single approved recipe is ``reviewed-work-track-position-metadata-v1``.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import unicodedata


POLICY = 'reviewed-work-track-position-metadata-v1'
WORK_LABEL = 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'
UPLOADER_CHANNEL_ID = 'UCOYQDzmJEWJHIuNosA2rxkg'
LOCAL_ALBUM = '星のカービィ スターアライズ オリジナル・サウンドトラック'
TITLE_RE = re.compile(
    r'^\s*(?P<position>[1-6]-\d{2})\.\s*(?P<english>.+?)\s+-\s*'
    + re.escape(WORK_LABEL) + r'\s*$')
ORDINAL_RE = re.compile(r'(?<!\d)[1-6]-\d{2}(?!\d)')
UNSAFE_VERSION_RE = re.compile(
    r'\b(?:extended|gamerip|game\s*rip|long\s+version|full\s+version|'
    r'karaoke|off[ _-]?vocal|instrumental|remix|remaster(?:ed|ing)?|'
    r'extended\s+mix|edit|medley|alternate|alt\.?|version|ver\.?|'
    r'arrange(?:d|ment)?|live|tv[ _-]?size|game[ _-]?size)\b|'
    r'ゲームリップ|カラオケ|オフボーカル|リミックス',
    re.IGNORECASE)
QUALIFIER_RE = re.compile(r'[\(\)\[\]{}（）【】]')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


class OfficialTracklistParser(HTMLParser):
    """Extract only official ``disc-track`` and Japanese title table cells."""
    def __init__(self):
        super().__init__()
        self.in_td = False
        self.cell = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == 'td':
            self.in_td = True
            self.cell = []
        elif tag == 'tr':
            self.row = []

    def handle_data(self, data):
        if self.in_td:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td':
            self.in_td = False
            self.row.append(''.join(self.cell).strip())
        elif tag == 'tr' and len(self.row) >= 2:
            position, title = self.row[0], self.row[1]
            if re.fullmatch(r'[1-6]-\d{2}', position):
                self.rows.append((position, title))


def parse_official_tracklist(html_bytes: bytes) -> dict[str, str]:
    parser = OfficialTracklistParser()
    parser.feed(html_bytes.decode('utf-8'))
    positions = [position for position, _ in parser.rows]
    if len(parser.rows) != 233 or len(set(positions)) != 233:
        raise ValueError('official work page must contain 233 unique disc-track rows')
    rows = dict(parser.rows)
    if any(not title for title in rows.values()):
        raise ValueError('official work page has an empty track title')
    return rows


def parse_work_title(title: object) -> dict | None:
    if not isinstance(title, str):
        return None
    # Exactly one ordinal and exactly one complete work label are required.
    ordinals = ORDINAL_RE.findall(title)
    if len(ordinals) != 1 or title.count(WORK_LABEL) != 1:
        return None
    match = TITLE_RE.fullmatch(title)
    if not match or UNSAFE_VERSION_RE.search(title):
        return None
    english = match.group('english').strip()
    if not english or UNSAFE_VERSION_RE.search(english) or QUALIFIER_RE.search(english):
        return None
    return {'disc_track': match.group('position'), 'english_title': english}


def _duration_ms(row: dict) -> int | None:
    value = row.get('length')
    if type(row.get('duration_seconds')) is int:
        value = row['duration_seconds'] * 1000
        return value if value > 0 else None
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,4}:\d{2}', value):
        return None
    minutes, seconds = map(int, value.split(':'))
    if seconds > 59:
        return None
    result = (minutes * 60 + seconds) * 1000
    return result if result > 0 else None


def _fixed_uploader(row: dict) -> bool:
    artists = row.get('artists')
    if (not isinstance(artists, list) or len(artists) != 1
            or not isinstance(artists[0], dict)):
        return False
    ids = [artists[0].get('id')]
    return ids == [UPLOADER_CHANNEL_ID]


def bind_video_row(row: dict, official: dict[str, str], local_tracks: list[dict],
                   *, duration_tolerance_ms: int = 3000) -> dict | None:
    """Return one strict metadata binding; it makes no identity assertion."""
    if not isinstance(row, dict) or not _fixed_uploader(row):
        return None
    parsed = parse_work_title(row.get('title'))
    if parsed is None:
        return None
    disc_track = parsed['disc_track']
    official_title = official.get(disc_track)
    if not isinstance(official_title, str) or not official_title:
        return None
    matches = [track for track in local_tracks
               if track.get('album') == LOCAL_ALBUM
               and isinstance(track.get('title'), str)
               and normalize(track['title']) == normalize(official_title)]
    if len(matches) != 1:
        return None
    local = matches[0]
    local_duration = local.get('duration_ms')
    remote_duration = _duration_ms(row)
    if (type(local_duration) is not int or local_duration <= 0 or remote_duration is None
            or abs(local_duration - remote_duration) > duration_tolerance_ms):
        return None
    video_id = row.get('videoId')
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return None
    artists = row.get('artists')
    return {
        'persistent_id': local['persistent_id'],
        'video_id': video_id,
        'disc_track': disc_track,
        'official_japanese_title': official_title,
        'local_title': local['title'],
        'local_artist': local.get('artist'),
        'local_album': local['album'],
        'local_duration_ms': local_duration,
        'remote_title': row['title'],
        'remote_artists': artists,
        'remote_album': row.get('album'),
        'remote_length': row.get('length'),
        'duration_delta_ms': abs(local_duration - remote_duration),
        'recording_identity_verified': False,
        'policy': POLICY,
    }


def bind_rows(rows: list[dict], official: dict[str, str],
              local_tracks: list[dict]) -> tuple[list[dict], dict]:
    """Bind strict rows, removing all PID/video collisions and duplicate positions."""
    provisional = []
    for position, row in enumerate(rows):
        binding = bind_video_row(row, official, local_tracks)
        if binding is not None:
            binding['response_position'] = position
            provisional.append(binding)
    pid_videos: dict[str, set[str]] = defaultdict(set)
    video_pids: dict[str, set[str]] = defaultdict(set)
    pid_positions: dict[str, set[str]] = defaultdict(set)
    for item in provisional:
        pid_videos[item['persistent_id']].add(item['video_id'])
        video_pids[item['video_id']].add(item['persistent_id'])
        pid_positions[item['persistent_id']].add(item['disc_track'])
    clean = [item for item in provisional
             if len(pid_videos[item['persistent_id']]) == 1
             and len(video_pids[item['video_id']]) == 1
             and len(pid_positions[item['persistent_id']]) == 1]
    stats = {
        'rows_seen': len(rows), 'strict_bindings_before_collision_filter': len(provisional),
        'unique_strict_bindings': len(clean),
        'ambiguous_pid_count': sum(len(v) > 1 for v in pid_videos.values()),
        'ambiguous_video_count': sum(len(v) > 1 for v in video_pids.values()),
        'multiple_positions_for_pid_count': sum(len(v) > 1 for v in pid_positions.values()),
    }
    return clean, stats


def ordered_radio_targets(rows: list[dict], seed_pid: str,
                          official: dict[str, str], local_tracks: list[dict]) -> tuple[list[dict], dict]:
    """Return nonempty candidate outgoing targets in radio response order."""
    bindings, stats = bind_rows(rows, official, local_tracks)
    targets = []
    seen_pids = set()
    for item in bindings:
        if item['persistent_id'] == seed_pid or item['persistent_id'] in seen_pids:
            continue
        seen_pids.add(item['persistent_id'])
        targets.append(item)
    stats['seed_self_rows_excluded'] = sum(x['persistent_id'] == seed_pid for x in bindings)
    stats['ordered_unique_target_count'] = len(targets)
    return targets, stats


def load_binding_inputs(binding_path: Path, report_path: Path, html_path: Path,
                        snapshot_path: Path, repo: Path | None = None):
    binding_bytes = binding_path.read_bytes()
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    doc = json.loads(binding_bytes)
    if report.get('status') != 'review_only_metadata_candidates':
        raise ValueError('binding source report is not review-only candidate evidence')
    summary = report.get('summary', {})
    if (summary.get('recording_identity_verified') is not False
            or summary.get('root_credit') != 0 or summary.get('edge_credit') != 0
            or summary.get('source_credit') != 0):
        raise ValueError('source report claims identity or graph credit')
    if sha256(binding_bytes) != report.get('candidate_bindings_sha256'):
        raise ValueError('candidate binding bytes do not match source report')
    if sha256(html_path.read_bytes()) != report.get('sources', {}).get('html_sha256'):
        raise ValueError('official page bytes do not match binding report')
    snapshot_bytes = snapshot_path.read_bytes()
    if sha256(snapshot_bytes) != report.get('inputs', {}).get('track_snapshot', {}).get('sha256'):
        raise ValueError('local snapshot bytes do not match binding report')
    snapshot = json.loads(snapshot_bytes)
    if snapshot.get('source_sha256') != report['inputs']['track_snapshot'].get('source_sha256'):
        raise ValueError('local source library hash differs from binding report')
    repo = (repo or Path.cwd()).resolve()
    for receipt_name in ('radio_request_file', 'radio_probe_report'):
        receipt = report.get('inputs', {}).get(receipt_name)
        if not isinstance(receipt, dict):
            raise ValueError(f'missing {receipt_name} input receipt')
        receipt_path = Path(receipt.get('path', ''))
        if not receipt_path.is_absolute():
            receipt_path = repo / receipt_path
        if (not receipt_path.is_file()
                or sha256(receipt_path.read_bytes()) != receipt.get('sha256')):
            raise ValueError(f'{receipt_name} bytes differ from binding report')
    official = parse_official_tracklist(html_path.read_bytes())
    request_receipt = report['inputs']['radio_request_file']
    probe_receipt = report['inputs']['radio_probe_report']
    request_path = Path(request_receipt['path'])
    if not request_path.is_absolute():
        request_path = repo / request_path
    raw_request = json.loads(request_path.read_bytes())
    probe_path = Path(probe_receipt['path'])
    if not probe_path.is_absolute():
        probe_path = repo / probe_path
    probe = json.loads(probe_path.read_bytes())
    args = raw_request.get('arguments')
    if (raw_request.get('endpoint') != 'get_watch_playlist'
            or not isinstance(args, dict) or args.get('radio') is not True
            or args.get('videoId') != probe.get('video_id')
            or args.get('limit') != 25
            or probe.get('account_authentication_verified') is not True
            or probe.get('selected_seed_returned') is not True
            or probe.get('http_requests') != 3):
        raise ValueError('parent probe and saved authenticated radio request do not agree')
    raw_rows = raw_request.get('response', {}).get('tracks')
    recomputed, _ = bind_rows(raw_rows if isinstance(raw_rows, list) else [],
                              official, snapshot['tracks'])
    def signature(row):
        return (row.get('response_position'), row.get('video_id'),
                row.get('persistent_id') or row.get('local_track', {}).get('persistent_id'),
                row.get('disc_track'), row.get('remote_title'),
                row.get('official_japanese_title'),
                row.get('local_title') or row.get('local_track', {}).get('title'),
                row.get('duration_delta_ms'),
                tuple((artist.get('id'), artist.get('name'))
                      for artist in row.get('remote_artists', row.get('uploader_artists', []))
                      if isinstance(artist, dict)))
    if [signature(row) for row in doc.get('bindings', [])] != [signature(row) for row in recomputed]:
        raise ValueError('candidate packet rows do not recompute from exact saved radio payload')
    return doc, report, official, snapshot['tracks'], {
        'candidate_bindings': {'path': str(binding_path), 'sha256': sha256(binding_bytes)},
        'binding_report': {'path': str(report_path), 'sha256': sha256(report_bytes)},
        'parent_candidate_report': {'path': str(report_path), 'sha256': sha256(report_bytes)},
        'official_html': {'path': str(html_path), 'sha256': sha256(html_path.read_bytes())},
        'track_snapshot': {'path': str(snapshot_path), 'sha256': sha256(snapshot_bytes)},
        'radio_request_receipt': {
            'path': report['inputs']['radio_request_file']['path'],
            'sha256': report['inputs']['radio_request_file']['sha256']},
        'radio_probe_report': {
            'path': report['inputs']['radio_probe_report']['path'],
            'sha256': report['inputs']['radio_probe_report']['sha256']},
    }


def load_frontier_watch_cache(path: Path, official: dict[str, str],
                              local_tracks: list[dict]) -> tuple[list[dict], dict]:
    """Load a saved radio response as a metadata hint only, never a proof."""
    raw = path.read_bytes()
    if len(raw) > 10_000_000:
        raise ValueError('frontier radio cache exceeds the bounded file size')
    document = json.loads(raw)
    if (not isinstance(document, dict)
            or set(document) != {'endpoint', 'arguments', 'observed_at', 'response'}
            or document.get('endpoint') != 'get_watch_playlist'):
        raise ValueError('frontier cache is not an exact saved watch-playlist response')
    arguments = document.get('arguments')
    if (not isinstance(arguments, dict)
            or set(arguments) != {'videoId', 'radio', 'limit'}
            or arguments.get('radio') is not True
            or type(arguments.get('limit')) is not int
            or arguments.get('limit') not in (25, 100)
            or not isinstance(arguments.get('videoId'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{11}', arguments['videoId'])):
        raise ValueError('frontier cache arguments are outside the approved radio-only profile')
    if not isinstance(document.get('observed_at'), str) or not document['observed_at']:
        raise ValueError('frontier cache lacks its saved observation timestamp')
    try:
        observed = document['observed_at'].replace('Z', '+00:00')
        from datetime import datetime
        parsed_time = datetime.fromisoformat(observed)
    except ValueError:
        raise ValueError('frontier cache timestamp is malformed') from None
    if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
        raise ValueError('frontier cache timestamp must include a timezone')
    cache_key = sha256(json.dumps(
        ['get_watch_playlist', arguments], ensure_ascii=False,
        sort_keys=True).encode())
    if path.parent.name != 'requests' or path.name != cache_key + '.json':
        raise ValueError('frontier cache filename does not match its request hash')
    response = document.get('response')
    rows = response.get('tracks') if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('frontier cache track list is missing or over the safe parser bound')
    bindings, counts = bind_rows(rows, official, local_tracks)
    digest = sha256(raw)
    for binding in bindings:
        binding['frontier_hint'] = {
            'path': str(path), 'sha256': digest,
            'response_position': binding['response_position'],
            'metadata_hint_only': True,
            'authentication_carried_forward': False,
            'credit': 0,
        }
    receipt = {'path': str(path), 'sha256': digest,
               'request_key': cache_key,
               'endpoint': document['endpoint'], 'arguments': arguments,
               'observed_at': document['observed_at'],
               'returned_track_count': len(rows),
               'strict_metadata_candidate_count': len(bindings),
               'metadata_hint_only': True, 'credit': 0,
               'binding_counts': counts}
    return bindings, receipt


def load_frontier_playlist_cache(path: Path, official: dict[str, str],
                                 local_tracks: list[dict]) -> tuple[list[dict], dict]:
    """Load the pinned Kirby get_playlist response as metadata hints only."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError('frontier playlist cache exceeds the bounded file size')
    expected_dir = 'post870-kirby-playlist-direct-20261015-v04'
    if path.parent.name != 'requests' or path.parent.parent.name != expected_dir:
        raise ValueError('playlist cache is outside the reviewed direct-fetch namespace')
    document = json.loads(raw)
    if (not isinstance(document, dict)
            or set(document) != {'endpoint', 'arguments', 'observed_at', 'response'}
            or document.get('endpoint') != 'get_playlist'):
        raise ValueError('frontier cache is not an exact saved get_playlist response')
    arguments = document.get('arguments')
    if arguments != {
            'playlistId': 'VLPLlvbNxiPAuN9SigrXnphMm_xov4fJ5Xya', 'limit': 250}:
        raise ValueError('playlist cache arguments are outside the reviewed fixed profile')
    if not isinstance(document.get('observed_at'), str) or not document['observed_at']:
        raise ValueError('playlist cache lacks its saved observation timestamp')
    try:
        from datetime import datetime
        parsed = datetime.fromisoformat(document['observed_at'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('playlist cache timestamp is malformed') from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('playlist cache timestamp must include a timezone')
    request_key = sha256(json.dumps(
        ['get_playlist', arguments], ensure_ascii=False, sort_keys=True).encode())
    if path.name != request_key + '.json':
        raise ValueError('playlist cache filename does not match its request hash')

    summary_path = path.parent.parent / 'summary.json'
    intent_path = path.parent.parent / 'intent.json'
    if not summary_path.is_file() or not intent_path.is_file():
        raise ValueError('playlist cache lacks its direct-fetch summary/intent')
    summary = json.loads(summary_path.read_bytes())
    intent = json.loads(intent_path.read_bytes())
    raw_sha = sha256(raw)
    if (summary.get('producer') != 'opengenius-kirby-direct-playlist-metadata-v1'
            or summary.get('status') != 'completed_metadata_hints_only'
            or summary.get('account_authentication_verified') is not True
            or summary.get('http_requests') != 5
            or summary.get('cumulative_http') != 5130
            or summary.get('root_credit') != 0 or summary.get('edge_credit') != 0
            or summary.get('source_credit') != 0
            or summary.get('playlist_request', {}).get('request_receipt_sha256') != raw_sha
            or summary.get('playlist_request', {}).get('request_receipt_path') != str(path)):
        raise ValueError('direct-fetch summary does not seal this metadata-only response')
    if (intent.get('producer') != 'opengenius-kirby-direct-playlist-fetch-intent-v1'
            or intent.get('playlist_id') != arguments['playlistId']
            or intent.get('playlist_title') != 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'
            or intent.get('search_repeated') is not False
            or intent.get('root_credit') != 0 or intent.get('edge_credit') != 0
            or intent.get('source_credit') != 0):
        raise ValueError('direct-fetch intent differs from the reviewed playlist profile')
    for input_path, expected_sha in intent.get('input_pins', {}).items():
        source = Path(input_path)
        if not source.is_file() or sha256(source.read_bytes()) != expected_sha:
            raise ValueError('a direct-fetch source input changed')

    response = document.get('response')
    rows = response.get('tracks') if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != 233:
        raise ValueError('direct playlist response must contain 233 tracks')
    # Playlist endpoint uses `duration`; watch_playlist uses `length`. Project
    # an in-memory copy into the common binder shape while retaining the raw
    # endpoint receipt unchanged. Refuse conflicting dual duration fields.
    normalized_rows = []
    duration_projection_count = 0
    for source_row in rows:
        if not isinstance(source_row, dict):
            normalized_rows.append(source_row)
            continue
        row = dict(source_row)
        if 'length' not in row:
            duration = row.get('duration')
            seconds = row.get('duration_seconds')
            if isinstance(duration, str) and re.fullmatch(r'\d{1,4}:\d{2}', duration):
                minutes, remainder = map(int, duration.split(':'))
                if remainder > 59:
                    raise ValueError('playlist duration has an invalid seconds field')
                if type(seconds) is int and seconds != minutes * 60 + remainder:
                    raise ValueError('playlist duration and duration_seconds disagree')
                row['length'] = duration
                duration_projection_count += 1
            elif type(seconds) is int and seconds > 0:
                row['length'] = f'{seconds // 60}:{seconds % 60:02d}'
                duration_projection_count += 1
        normalized_rows.append(row)
    bindings, counts = bind_rows(normalized_rows, official, local_tracks)
    for binding in bindings:
        binding['frontier_hint'] = {
            'path': str(path), 'sha256': raw_sha,
            'response_position': binding['response_position'],
            'metadata_hint_only': True,
            'authentication_carried_forward': False,
            'recording_identity_verified': False,
            'credit': 0,
        }
    receipt = {
        'path': str(path), 'sha256': raw_sha, 'request_key': request_key,
        'summary_path': str(summary_path), 'summary_sha256': sha256(summary_path.read_bytes()),
        'intent_path': str(intent_path), 'intent_sha256': sha256(intent_path.read_bytes()),
        'endpoint': document['endpoint'], 'arguments': arguments,
        'observed_at': document['observed_at'], 'returned_track_count': len(rows),
        'duration_field_projection_count': duration_projection_count,
        'strict_metadata_candidate_count': len(bindings),
        'metadata_hint_only': True, 'authentication_carried_forward': False,
        'recording_identity_verified': False, 'credit': 0, 'binding_counts': counts,
    }
    if receipt['strict_metadata_candidate_count'] != 99:
        raise ValueError('playlist binding count differs from the reviewed 99-row result')
    return bindings, receipt


# Versioned extension for the single root-approved, track-scoped Kirby source
# annotation recipe.  The legacy loader above remains unchanged and must still
# validate the original 99-row packet before this extension can run.
ORIGIN_SUFFIX_POLICY = 'reviewed-work-track-origin-suffix-metadata-v1'
ORIGIN_SUFFIX_PROPOSAL_PATH = Path(
    '/mnt/data/mizoyuzu/Projects/personal/opengenius/data/ytmusic/'
    'post870-metadata-opportunities-20261015-v01/'
    'review-ready-v02-origin-suffix-proposals/proposals-v02.json')
ORIGIN_SUFFIX_PROPOSAL_SHA256 = (
    '27609c6bfe00edc3fe1a78ef00313536282e93ba49bb88713442073e26f8ea3c')
ORIGIN_SUFFIX_DECISIONS_PATH = ORIGIN_SUFFIX_PROPOSAL_PATH.with_name(
    'root-review-decisions.json')
ORIGIN_SUFFIX_DECISIONS_SHA256 = (
    '3196ad885cb55e0ee2bd1f3ad5d95fef4c0664425e6596c9c895ae73ddd27821')
ORIGIN_SUFFIX_PLAYLIST_PATH = Path(
    '/mnt/data/mizoyuzu/Projects/personal/opengenius/data/ytmusic/'
    'post870-kirby-playlist-direct-20261015-v04/requests/'
    'ae16d1c9d68a4b5a7cea47b4e9b9a1a1523c7a11d4781d5e02a59c7a6bb3ffd3.json')
ORIGIN_SUFFIX_PLAYLIST_SHA256 = (
    '4091494d3e0d8ae9c3c02fbafaf0b5f6d4285a7b31ca762210e87a6699722d0a')
ORIGIN_SUFFIXES = frozenset({
    '星のカービィ', '星のカービィ2', '星のカービィ3', '星のカービィ Wii',
    'カービィのすいこみ大作戦', 'デデデ大王のデデデでデンＺ',
    'みんなで！カービィハンターズＺ', 'カービィファイターズＺ',
    '星のカービィ スーパーデラックス', '星のカービィ64',
    '星のカービィ 鏡の大迷宮', '星のカービィ 参上！ドロッチェ団',
    'タッチ！カービィ', '毛糸のカービィ',
    '星のカービィ トリプルデラックス', '星のカービィ ロボボプラネット',
})
ORIGIN_SUFFIX_TITLE_PREFIX_RE = re.compile(r'^\s*([1-6]-\d{2})\.\s*\S')


def _project_playlist_rows(rows: list[dict]) -> list[dict]:
    """Project the playlist's duration field without altering saved source bytes."""
    projected = []
    for source_row in rows:
        if not isinstance(source_row, dict):
            projected.append(source_row)
            continue
        row = dict(source_row)
        if 'length' not in row:
            duration = row.get('duration')
            seconds = row.get('duration_seconds')
            if isinstance(duration, str) and re.fullmatch(r'\d{1,4}:\d{2}', duration):
                minutes, remainder = map(int, duration.split(':'))
                if remainder > 59:
                    raise ValueError('playlist duration has an invalid seconds field')
                if type(seconds) is int and seconds != minutes * 60 + remainder:
                    raise ValueError('playlist duration and duration_seconds disagree')
                row['length'] = duration
            elif type(seconds) is int and seconds > 0:
                row['length'] = f'{seconds // 60}:{seconds % 60:02d}'
        projected.append(row)
    return projected


def _suffix_base_title(official_title: str, source_suffix: str) -> str:
    """Remove only the exact terminal annotation named by the reviewed tuple."""
    if source_suffix not in ORIGIN_SUFFIXES:
        raise ValueError('origin suffix is not in the fixed reviewed allowlist')
    alternatives = (f'（{source_suffix}）', f'({source_suffix})')
    for ending in alternatives:
        if official_title.endswith(ending):
            return official_title[:-len(ending)].rstrip()
    raise ValueError('official title does not end in the exact reviewed source suffix')


def validate_origin_suffix_candidate(candidate: dict, source_row: dict,
                                     official: dict[str, str],
                                     local_tracks: list[dict], *,
                                     response_position: int,
                                     source_path: Path,
                                     source_sha256: str) -> dict:
    """Recompute one reviewed tuple from the pinned playlist and local snapshot."""
    if not isinstance(candidate, dict) or not isinstance(source_row, dict):
        raise ValueError('origin-suffix candidate and source row must be objects')
    ordinal = candidate.get('ordinal')
    match = ORIGIN_SUFFIX_TITLE_PREFIX_RE.match(source_row.get('title', ''))
    if (not isinstance(ordinal, str) or match is None
            or match.group(1) != ordinal or official.get(ordinal) != candidate.get('official_title')):
        raise ValueError('candidate PID/ordinal does not match the exact official playlist row')
    suffix = candidate.get('source_game_suffix')
    base_title = _suffix_base_title(candidate['official_title'], suffix)
    if candidate.get('candidate_base_title') != base_title:
        raise ValueError('candidate base title differs from exact suffix removal')
    if not _fixed_uploader(source_row):
        raise ValueError('origin-suffix row does not have the fixed reviewed uploader ID')
    video_id = source_row.get('videoId')
    if (not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id)
            or candidate.get('video_id') != video_id):
        raise ValueError('candidate video ID differs from its exact playlist row')
    remote_duration = _duration_ms(source_row)
    if remote_duration is None or candidate.get('remote_duration_ms') != remote_duration:
        raise ValueError('candidate remote duration differs from its exact playlist row')
    matches = [track for track in local_tracks
               if track.get('album') == LOCAL_ALBUM
               and isinstance(track.get('title'), str)
               and normalize(track['title']) == normalize(base_title)]
    if len(matches) != 1:
        raise ValueError('suffix-stripped title does not resolve to one exact local album row')
    local = matches[0]
    local_pid = local.get('persistent_id')
    if (candidate.get('persistent_id') != local_pid
            or candidate.get('local_title') != local.get('title')
            or candidate.get('local_album') != LOCAL_ALBUM
            or candidate.get('local_duration_ms') != local.get('duration_ms')):
        raise ValueError('candidate PID/title/album/duration differs from the unique local row')
    if sum(track.get('persistent_id') == local_pid for track in local_tracks) != 1:
        raise ValueError('candidate persistent ID is not globally unique in the snapshot')
    local_duration = local.get('duration_ms')
    if type(local_duration) is not int or local_duration <= 0:
        raise ValueError('local track duration is invalid')
    delta = abs(local_duration - remote_duration)
    if delta > 3000 or candidate.get('duration_delta_ms') != delta:
        raise ValueError('candidate duration difference exceeds or differs from the 3-second gate')
    if (candidate.get('source_playlist_id') != 'VLPLlvbNxiPAuN9SigrXnphMm_xov4fJ5Xya'
            or candidate.get('source_album_title') != WORK_LABEL
            or candidate.get('source_request_endpoint') != 'get_playlist'):
        raise ValueError('candidate is not scoped to the exact reviewed Kirby OST playlist')
    artists = source_row.get('artists')
    return {
        'persistent_id': local_pid,
        'video_id': video_id,
        'disc_track': ordinal,
        'official_japanese_title': candidate['official_title'],
        'source_game_suffix': suffix,
        'candidate_base_title': base_title,
        'local_title': local['title'],
        'local_artist': local.get('artist'),
        'local_album': local['album'],
        'local_duration_ms': local_duration,
        'remote_title': source_row['title'],
        'remote_artists': artists,
        'remote_album': source_row.get('album'),
        'remote_length': source_row.get('length'),
        'duration_delta_ms': delta,
        'response_position': response_position,
        'recording_identity_verified': False,
        'policy': ORIGIN_SUFFIX_POLICY,
        'origin_suffix_evidence': {
            'path': str(ORIGIN_SUFFIX_PROPOSAL_PATH),
            'sha256': ORIGIN_SUFFIX_PROPOSAL_SHA256,
            'root_decisions_path': str(ORIGIN_SUFFIX_DECISIONS_PATH),
            'root_decisions_sha256': ORIGIN_SUFFIX_DECISIONS_SHA256,
            'playlist_path': str(source_path),
            'playlist_sha256': source_sha256,
            'playlist_row': response_position + 1,
            'ordinal': ordinal,
            'official_japanese_title': candidate['official_title'],
            'source_game_suffix': suffix,
            'credit': 0,
        },
    }


def bind_video_row_with_origin_suffix(row: dict, official: dict[str, str],
                                     local_tracks: list[dict],
                                     scoped_aliases: list[dict]) -> dict | None:
    """Use a scoped alias only for its exact, previously reviewed source video."""
    video_id = row.get('videoId') if isinstance(row, dict) else None
    matches = [item for item in scoped_aliases if item.get('video_id') == video_id]
    if matches:
        if len(matches) != 1 or not _fixed_uploader(row):
            return None
        expected = matches[0]
        evidence = expected.get('origin_suffix_evidence')
        ordinal = expected.get('disc_track')
        official_title = expected.get('official_japanese_title')
        source_suffix = expected.get('source_game_suffix')
        if not isinstance(ordinal, str) or not re.fullmatch(r'[1-6]-\d{2}', ordinal):
            return None
        title_match = ORIGIN_SUFFIX_TITLE_PREFIX_RE.match(
            expected.get('remote_title', ''))
        try:
            expected_base = _suffix_base_title(official_title, source_suffix)
        except (TypeError, ValueError):
            return None
        local_matches = [track for track in local_tracks
                         if track.get('persistent_id') == expected.get('persistent_id')
                         and track.get('album') == LOCAL_ALBUM
                         and track.get('title') == expected.get('local_title')
                         and track.get('duration_ms') == expected.get('local_duration_ms')
                         and normalize(track.get('title', '')) == normalize(expected_base)]
        # The exact raw playlist title, including its original-game annotation,
        # carries the reviewed ordinal into both fresh radio and watch rows.
        # Keep that title byte-for-byte; never strip arbitrary qualifiers here.
        if (expected.get('policy') != ORIGIN_SUFFIX_POLICY
                or not isinstance(evidence, dict)
                or evidence.get('path') != str(ORIGIN_SUFFIX_PROPOSAL_PATH)
                or evidence.get('sha256') != ORIGIN_SUFFIX_PROPOSAL_SHA256
                or evidence.get('root_decisions_path') != str(ORIGIN_SUFFIX_DECISIONS_PATH)
                or evidence.get('root_decisions_sha256') != ORIGIN_SUFFIX_DECISIONS_SHA256
                or evidence.get('ordinal') != ordinal
                or evidence.get('official_japanese_title') != official_title
                or evidence.get('source_game_suffix') != source_suffix
                or evidence.get('credit') != 0
                or len(local_matches) != 1
                or title_match is None or title_match.group(1) != ordinal
                or official.get(ordinal) != official_title):
            return None
        # The source video ID, fixed channel and exact observed title all remain
        # bound. No suffix removal is performed against a fresh response.
        if (row.get('title') != expected.get('remote_title')
                or row.get('videoId') != expected.get('video_id')):
            return None
        remote_duration = _duration_ms(row)
        if remote_duration is None or abs(expected['local_duration_ms'] - remote_duration) > 3000:
            return None
        if row.get('duration_seconds') is None and row.get('length') is None:
            return None
        result = dict(expected)
        result.update({
            'remote_title': row['title'], 'remote_artists': row.get('artists'),
            'remote_album': row.get('album'), 'remote_length': row.get('length'),
            'duration_delta_ms': abs(expected['local_duration_ms'] - remote_duration),
            'recording_identity_verified': False,
        })
        return result
    return bind_video_row(row, official, local_tracks)


def bind_rows_with_origin_suffix(rows: list[dict], official: dict[str, str],
                                 local_tracks: list[dict],
                                 scoped_aliases: list[dict]) -> tuple[list[dict], dict]:
    """Bind ordinary rows normally and the exact reviewed 40 video tuples only."""
    provisional = []
    for position, row in enumerate(rows):
        binding = bind_video_row_with_origin_suffix(
            row, official, local_tracks, scoped_aliases)
        if binding is not None:
            binding['response_position'] = position
            provisional.append(binding)
    pid_videos: dict[str, set[str]] = defaultdict(set)
    video_pids: dict[str, set[str]] = defaultdict(set)
    pid_positions: dict[str, set[str]] = defaultdict(set)
    for item in provisional:
        pid_videos[item['persistent_id']].add(item['video_id'])
        video_pids[item['video_id']].add(item['persistent_id'])
        pid_positions[item['persistent_id']].add(item['disc_track'])
    clean = [item for item in provisional
             if len(pid_videos[item['persistent_id']]) == 1
             and len(video_pids[item['video_id']]) == 1
             and len(pid_positions[item['persistent_id']]) == 1]
    return clean, {
        'rows_seen': len(rows),
        'strict_bindings_before_collision_filter': len(provisional),
        'unique_strict_bindings': len(clean),
        'ambiguous_pid_count': sum(len(v) > 1 for v in pid_videos.values()),
        'ambiguous_video_count': sum(len(v) > 1 for v in video_pids.values()),
        'multiple_positions_for_pid_count': sum(len(v) > 1 for v in pid_positions.values()),
    }


def ordered_radio_targets_with_origin_suffix(rows: list[dict], seed_pid: str,
                                             official: dict[str, str],
                                             local_tracks: list[dict],
                                             scoped_aliases: list[dict]):
    bindings, stats = bind_rows_with_origin_suffix(
        rows, official, local_tracks, scoped_aliases)
    targets, seen = [], set()
    for item in bindings:
        pid = item['persistent_id']
        if pid == seed_pid or pid in seen:
            continue
        seen.add(pid)
        targets.append(item)
    stats['seed_self_rows_excluded'] = sum(
        item['persistent_id'] == seed_pid for item in bindings)
    stats['ordered_unique_target_count'] = len(targets)
    return targets, stats


def load_frontier_playlist_cache_v04(path: Path, official: dict[str, str],
                                     local_tracks: list[dict]):
    """Legacy 99 validation plus the separately root-approved 40 exact tuples."""
    path = Path(path).resolve()
    legacy, base_receipt = load_frontier_playlist_cache(path, official, local_tracks)
    proposal_bytes = ORIGIN_SUFFIX_PROPOSAL_PATH.read_bytes()
    decision_bytes = ORIGIN_SUFFIX_DECISIONS_PATH.read_bytes()
    proposal_hash, decision_hash = sha256(proposal_bytes), sha256(decision_bytes)
    if proposal_hash != ORIGIN_SUFFIX_PROPOSAL_SHA256:
        raise ValueError('root-reviewed origin-suffix proposal bytes changed')
    if decision_hash != ORIGIN_SUFFIX_DECISIONS_SHA256:
        raise ValueError('root approval decision bytes changed')
    proposal, decisions = json.loads(proposal_bytes), json.loads(decision_bytes)
    if (proposal.get('producer') != 'opengenius-kirby-official-origin-suffix-proposals-v1'
            or proposal.get('status') != 'review_candidates_only'
            or proposal.get('candidate_count') != 40
            or proposal.get('alias_applied') is not False
            or proposal.get('root_credit') != 0 or proposal.get('edge_credit') != 0
            or proposal.get('identity_proof_credit') != 0
            or proposal.get('database_written') is not False
            or decisions.get('status') != 'root_approved_track_scoped_metadata_aliases_only'
            or decisions.get('proposal_sha256') != proposal_hash
            or decisions.get('approved_count') != 40):
        raise ValueError('proposal/approval reports do not have the exact reviewed no-credit shape')
    proposal_rows = proposal.get('candidates')
    approved_rows = decisions.get('approved_candidates')
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'))
    if (not isinstance(proposal_rows, list) or len(proposal_rows) != 40
            or not isinstance(approved_rows, list) or len(approved_rows) != 40
            or sorted(map(canonical, proposal_rows)) != sorted(map(canonical, approved_rows))):
        raise ValueError('proposal tuples differ from the root-approved 40-row set')
    raw_doc = json.loads(path.read_bytes())
    raw_rows = raw_doc['response']['tracks']
    projected = _project_playlist_rows(raw_rows)
    normalized_by_pid = {}
    for candidate in proposal_rows:
        position = candidate.get('playlist_row')
        if type(position) is not int or not 1 <= position <= len(projected):
            raise ValueError('approved playlist row position is invalid')
        item = validate_origin_suffix_candidate(
            candidate, projected[position - 1], official, local_tracks,
            response_position=position - 1, source_path=path,
            source_sha256=base_receipt['sha256'])
        pid = item['persistent_id']
        if pid in normalized_by_pid:
            raise ValueError('approved origin-suffix tuples duplicate a local PID')
        normalized_by_pid[pid] = item
    extension = list(normalized_by_pid.values())
    all_items = legacy + extension
    pids = [item['persistent_id'] for item in all_items]
    videos = [item['video_id'] for item in all_items]
    if len(set(pids)) != 139 or len(set(videos)) != 139:
        raise ValueError('legacy 99 plus approved 40 tuples are not globally unique')
    for item in all_items:
        item['frontier_hint'] = {
            'path': str(path), 'sha256': base_receipt['sha256'],
            'response_position': item['response_position'],
            'metadata_hint_only': True, 'authentication_carried_forward': False,
            'recording_identity_verified': False, 'credit': 0,
        }
    receipt = dict(base_receipt)
    receipt.update({
        'legacy_v03_strict_candidate_count': base_receipt['strict_metadata_candidate_count'],
        'origin_suffix_v04_candidate_count': len(extension),
        'strict_metadata_candidate_count': len(all_items),
        'origin_suffix_proposal': {
            'path': str(ORIGIN_SUFFIX_PROPOSAL_PATH), 'sha256': proposal_hash,
            'root_decisions_path': str(ORIGIN_SUFFIX_DECISIONS_PATH),
            'root_decisions_sha256': decision_hash,
            'policy': ORIGIN_SUFFIX_POLICY,
            'candidate_count': len(extension), 'credit': 0,
        },
        'metadata_hint_only': True, 'authentication_carried_forward': False,
        'recording_identity_verified': False, 'credit': 0,
    })
    return all_items, receipt


class ReviewedRecipeContext:
    """Validated row binders for the pinned legacy+origin-suffix frontier."""
    def __init__(self, official: dict[str, str], local_tracks: list[dict],
                 frontier_bindings: list[dict], receipt: dict):
        self.official = dict(official)
        self.local_tracks = tuple(copy.deepcopy(local_tracks))
        self.frontier_bindings = tuple(copy.deepcopy(frontier_bindings))
        self.origin_suffix_bindings = tuple(
            copy.deepcopy(item) for item in frontier_bindings
            if item.get('policy') == ORIGIN_SUFFIX_POLICY)
        self.legacy_bindings = tuple(
            copy.deepcopy(item) for item in frontier_bindings
            if item.get('policy') == POLICY)
        self.receipt = copy.deepcopy(receipt)
        if (len(self.frontier_bindings) != 139
                or len(self.legacy_bindings) != 99
                or len(self.origin_suffix_bindings) != 40
                or receipt.get('origin_suffix_v04_candidate_count') != 40
                or receipt.get('legacy_v03_strict_candidate_count') != 99):
            raise ValueError('reviewed recipe context does not contain the exact 99+40 frontier')

    def bind_rows(self, rows: list[dict]) -> tuple[list[dict], dict]:
        return bind_rows_with_origin_suffix(
            rows, self.official, list(self.local_tracks),
            copy.deepcopy(list(self.origin_suffix_bindings)))

    def ordered_radio_targets(self, rows: list[dict], seed_pid: str):
        return ordered_radio_targets_with_origin_suffix(
            rows, seed_pid, self.official, list(self.local_tracks),
            copy.deepcopy(list(self.origin_suffix_bindings)))


def load_reviewed_recipe_context(official: dict[str, str],
                                 local_tracks: list[dict]) -> ReviewedRecipeContext:
    """Reload the pinned raw packet and return audited binders for fresh rows.

    The caller supplies only the source-backed official/local metadata already
    loaded for the capture. The function itself reads and revalidates the exact
    saved playlist, 99 legacy rows, and 40 root-approved suffix tuples.
    """
    if (not ORIGIN_SUFFIX_PLAYLIST_PATH.is_file()
            or sha256(ORIGIN_SUFFIX_PLAYLIST_PATH.read_bytes())
            != ORIGIN_SUFFIX_PLAYLIST_SHA256):
        raise ValueError('pinned origin-suffix playlist bytes changed or are missing')
    frontier, receipt = load_frontier_playlist_cache_v04(
        ORIGIN_SUFFIX_PLAYLIST_PATH, official, local_tracks)
    if receipt.get('sha256') != ORIGIN_SUFFIX_PLAYLIST_SHA256:
        raise ValueError('reviewed recipe context has a different playlist receipt')
    return ReviewedRecipeContext(official, local_tracks, frontier, receipt)


def load_skip_seed_pids(path: Path | None, local_tracks: list[dict]) -> tuple[set[str], dict | None]:
    if path is None:
        return set(), None
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError('skip PID list exceeds the bounded file size')
    pids = json.loads(raw)
    known = {track['persistent_id'] for track in local_tracks}
    if (not isinstance(pids, list) or len(pids) > len(known)
            or any(not isinstance(pid, str) or not re.fullmatch(r'[0-9A-F]{16}', pid)
                   or pid not in known for pid in pids)
            or len(set(pids)) != len(pids)):
        raise ValueError('skip PID file must be a unique JSON list of current Library PIDs')
    return set(pids), {'path': str(path), 'sha256': sha256(raw),
                       'persistent_id_count': len(pids),
                       'applies_to': 'seed selection only; matching PIDs remain valid radio targets',
                       'credit': 0}

