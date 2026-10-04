"""Classification editor and offline recommendation review."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from music_clusters import _unique_keys, classify_tracks, scope_graphs
from music_identity_map import IdentityMap, load_library
from probe_ytmusic import normalize

PROFILES = ('without-compatible-genre', 'artist-album-minimum-one', 'relations-only')
WEB = Path(__file__).resolve().parents[1] / 'web' / 'music-review.html'


class ReviewSession:
    def __init__(self, snapshot, config=None, output_directory=None, evaluation=None, engine=None):
        self.tracks, self.library_hash, self.provenance = load_library(track_snapshot=snapshot)
        self.config = (json.loads(config.read_bytes(), object_pairs_hook=_unique_keys) if config else
                       {'schema_version': 1, 'source_library_sha256': self.library_hash, 'rules': [], 'overrides': {}})
        classify_tracks(self.config, self.tracks, self.library_hash)
        self.output_directory = output_directory or Path('data/music-review')
        self.engine = engine
        self.lock = threading.Lock()
        self.latest = None
        self.saved_config_sha256 = self.config_hash()
        if evaluation:
            self.latest = json.loads(evaluation.read_bytes())
            if self.latest.get('library_sha256') != self.library_hash:
                raise ValueError('Evaluation belongs to a different Library')

    def state(self):
        return {'tracks': self.tracks, 'config': self.config,
                'classifications': classify_tracks(self.config, self.tracks, self.library_hash),
                'library_sha256': self.library_hash, 'evaluation': self.latest,
                'evaluation_available': self.engine is not None,
                'available_root_pids': sorted({g['root_pid'] for g in self.engine[0]}) if self.engine else [],
                'config_sha256': self.config_hash(),
                'has_unsaved_changes': self.config_hash() != self.saved_config_sha256, 'profiles': list(PROFILES)}

    def config_hash(self):
        return hashlib.sha256(json.dumps(self.config, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def edit(self, payload):
        """Batch edits become PID overrides; unrelated rules and overrides survive."""
        if not isinstance(payload, dict) or set(payload) - {'persistent_ids', 'tags', 'kind', 'tag_mode', 'reset'}:
            raise ValueError('Invalid edit fields')
        pids = payload.get('persistent_ids')
        known = {track['persistent_id'] for track in self.tracks}
        if (not isinstance(pids, list) or not pids or any(not isinstance(pid, str) or pid not in known for pid in pids)
                or len(set(pids)) != len(pids)):
            raise ValueError('Select known, unique tracks')
        if 'reset' in payload and type(payload['reset']) is not bool:
            raise ValueError('Invalid reset flag')
        if payload.get('reset') and set(payload) != {'persistent_ids', 'reset'}:
            raise ValueError('Reset cannot include edits')
        if not payload.get('reset') and not ({'tags', 'kind'} & set(payload)):
            raise ValueError('Specify tags or kind')
        mode = payload.get('tag_mode', 'add')
        if mode not in ('add', 'replace'):
            raise ValueError('Invalid tag mode')
        proposed = copy.deepcopy(self.config)
        overrides = proposed.setdefault('overrides', {})
        labels = classify_tracks(self.config, self.tracks, self.library_hash)
        for pid in pids:
            if payload.get('reset'):
                overrides.pop(pid, None)
                continue
            override = overrides.setdefault(pid, {})
            if 'tags' in payload:
                tags = payload['tags']
                if not isinstance(tags, list) or any(not isinstance(tag, str) or not normalize(tag) for tag in tags):
                    raise ValueError('Tags must be nonempty strings')
                if len({normalize(tag) for tag in tags}) != len(tags):
                    raise ValueError('Duplicate tags')
                current = list(labels[pid]['tags']) if mode == 'add' else []
                keys = {normalize(tag) for tag in current}
                for tag in tags:
                    if normalize(tag) not in keys:
                        current.append(tag)
                        keys.add(normalize(tag))
                override['tags'] = current
            if 'kind' in payload:
                override['kind'] = payload['kind']
        # Validate entire edit before updating anything.
        classify_tracks(proposed, self.tracks, self.library_hash)
        self.config = proposed
        return self.state()

    def _save(self, prefix, document):
        self.output_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = self.output_directory / f'{prefix}-{stamp}-{secrets.token_hex(3)}.json'
        with path.open('x', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        return str(path.resolve())

    def save(self):
        path = self._save('clusters', self.config)
        self.saved_config_sha256 = self.config_hash()
        return {'path': path, 'config_sha256': self.saved_config_sha256}

    def evaluate(self, payload):
        if self.engine is None:
            raise ValueError('Offline evaluation inputs were not configured')
        if not isinstance(payload, dict) or set(payload) - {'tags', 'kind', 'excluded_kinds', 'profile', 'root_pid'}:
            raise ValueError('Invalid evaluation fields')
        tags, kind = payload.get('tags', []), payload.get('kind')
        profile = payload.get('profile', PROFILES[0])
        if profile not in PROFILES:
            raise ValueError('Unsupported profile')
        graphs, matcher, config, executable, provenance = self.engine
        labels = classify_tracks(self.config, self.tracks, self.library_hash)
        filtered, scope = scope_graphs(graphs, labels, tags, kind, excluded_kinds=payload.get('excluded_kinds', []))
        root = payload.get('root_pid')
        if root is not None:
            if not isinstance(root, str):
                raise ValueError('Invalid root')
            # Keep other roots' edges for observed two-hop recommendations.
            if root not in {graph['root_pid'] for graph in filtered}:
                raise ValueError('Selected root has no observations in this scope')
        from evaluate_ytmusic_batch import evaluate_graphs
        evaluated = evaluate_graphs(filtered, matcher, config, executable, profile=profile)
        if root is not None:
            evaluated['root_results'] = [row for row in evaluated['root_results'] if row['root_pid'] == root]
            evaluated['root_overlaps'] = []
        report = {'schema_version': 1, 'library_sha256': self.library_hash, 'library_input': self.provenance,
                  'cluster_config_sha256': self.config_hash(), 'cluster_config': copy.deepcopy(self.config),
                  'cluster_scope': scope,
                  'input_provenance': provenance, 'profile': profile, **evaluated,
                  'network_requests': 0, 'identity_status': 'unverified',
                  'music_app_acceptance_verified': False, 'ipod_acceptance_verified': False}
        path = self._save('evaluation', report)
        self.latest = report
        return {'path': path, 'evaluation': report}


def make_handler(session, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def same_origin(self):
            origin = urlsplit(self.headers.get('Origin', ''))
            return (origin.scheme in ('http', 'https') and origin.netloc == self.headers.get('Host')
                    and not origin.path and not origin.query and not origin.fragment)

        def do_GET(self):
            if self.path == '/':
                self.send(200, WEB.read_bytes().replace(b'__SESSION_TOKEN__', token.encode()), 'text/html; charset=utf-8')
            elif self.path == '/api/state':
                with session.lock:
                    self.send(200, session.state())
            else:
                self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if (not self.same_origin()
                    or self.headers.get('X-Review-Token') != token):
                self.send(403, {'error': 'Invalid local session'})
                return
            if self.headers.get('Content-Type') != 'application/json':
                self.send(415, {'error': 'JSON required'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(length), object_pairs_hook=_unique_keys)
                with session.lock:
                    if self.path == '/api/edit':
                        result = session.edit(payload)
                    elif self.path == '/api/save':
                        if payload != {}:
                            raise ValueError('Save takes no fields')
                        result = session.save()
                    elif self.path == '/api/evaluate':
                        result = session.evaluate(payload)
                    else:
                        self.send(404, {'error': 'Not found'})
                        return
                self.send(200, result)
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                messages = {
                    'Unknown scope tag': '指定したタグがありません。先に曲へタグを付けてください。',
                    'No roots remain in the requested cluster scope': 'この範囲には取得済みの起点がありません。',
                    'Selected root has no observations in this scope': '選んだ起点が範囲外です。条件を見直してください。',
                    'Duplicate tags': '同じタグが重複しています。',
                    'Tags must be nonempty strings': 'タグ名を確認してください。',
                    'Requested kind is also excluded': '対象の種類と除外の種類が同じです。',
                }
                self.send(400, {'error': messages.get(str(error), str(error))})
            except Exception:
                # No paths, raw library fields or authentication details in error responses.
                self.send(500, {'error': '処理に失敗しました。入力ファイルと出力先を確認してください。'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--evaluation', type=Path)
    parser.add_argument('--output-directory', type=Path, default=Path('data/music-review'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1')
    for name in ('identity-map', 'observations-directory', 'genius-reference', 'executable'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    evaluation_args = (args.identity_map, args.observations_directory, args.genius_reference, args.executable)
    if any(evaluation_args) and not all(evaluation_args):
        parser.error('Offline generation needs all four evaluation inputs')
    session = ReviewSession(args.track_snapshot, args.config, args.output_directory, args.evaluation)
    if all(evaluation_args):
        from emulate_genius import database_rows
        from evaluate_ytmusic_batch import collect_graphs, observation_paths
        map_bytes = args.identity_map.read_bytes()
        matcher = IdentityMap(json.loads(map_bytes), session.tracks, session.library_hash)
        paths, manifest_hash = observation_paths(args.observations_directory, [], session.library_hash)
        graphs, skipped, inputs = collect_graphs(paths, matcher, session.library_hash)
        reference = args.genius_reference.read_bytes()
        config, _, _ = database_rows(reference)
        provenance = {'observations': inputs, 'skipped_snapshots': skipped, 'seed_manifest_sha256': manifest_hash,
                      'identity_map_sha256': hashlib.sha256(map_bytes).hexdigest(),
                      'genius_reference_sha256': hashlib.sha256(reference).hexdigest(),
                      'executable_sha256': hashlib.sha256(args.executable.read_bytes()).hexdigest()}
        session.engine = (graphs, matcher, config, args.executable, provenance)
    token = secrets.token_hex(32)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(session, token))
    print(f'http://127.0.0.1:{server.server_port} — {len(session.tracks)}曲 / ローカル分類・推薦確認', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
