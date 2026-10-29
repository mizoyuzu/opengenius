"""Review-only bindings between an official work tracklist and video metadata.

This module never verifies recording identity and never writes an identity map.
Its single approved recipe is ``reviewed-work-track-position-metadata-v1``.
"""
from __future__ import annotations

from collections import defaultdict
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

