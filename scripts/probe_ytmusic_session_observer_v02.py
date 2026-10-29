"""Correctly classify requests made through requests.Session.post().

The underlying paced session accounts for every call. This adapter only repairs
the endpoint label when requests supplies ``request(method, url, ...)``.
It never stores query strings, headers, cookies, bodies, or response content.
"""
from __future__ import annotations

import json
import hashlib
import re
from urllib.parse import urlsplit

from probe_ytmusic_batch import _http_request_category, create_paced_session


def _request_parts(args, kwargs):
    method = kwargs.get('method')
    url = kwargs.get('url')
    if len(args) >= 2 and isinstance(args[0], str) and args[0].upper() in {
            'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
        method, url = args[0], args[1]
    elif args:
        if url is None:
            url = args[0]
        if method is None:
            method = 'GET'
    body = kwargs.get('json')
    if body is None:
        data = kwargs.get('data')
        if isinstance(data, bytes):
            try:
                body = json.loads(data.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
        elif isinstance(data, str):
            try:
                body = json.loads(data)
            except json.JSONDecodeError:
                body = None
    if not isinstance(url, str):
        raise ValueError('HTTP request did not include a URL')
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc or (parts.path and not parts.path.startswith('/')):
        raise ValueError('HTTP request URL is malformed')
    return str(method or 'GET').upper(), parts.path or '/', url, body


def _raw_album_candidates(raw_body, content_type):
    """Project only album browse IDs and titles from a raw search JSON body."""
    if 'json' not in content_type.lower() or len(raw_body) > 5_000_000:
        return []
    try:
        document = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []

    rows = []
    seen_ids = set()
    visited = 0

    def text_value(value):
        if isinstance(value, str):
            return value[:512] if value.strip() else None
        if isinstance(value, dict):
            simple = value.get('simpleText')
            if isinstance(simple, str) and simple.strip():
                return simple[:512]
            runs = value.get('runs')
            if isinstance(runs, list):
                pieces = [r.get('text') for r in runs if isinstance(r, dict)
                          and isinstance(r.get('text'), str)]
                joined = ''.join(pieces).strip()
                return joined[:512] if joined else None
        return None

    def candidate(node):
        renderer = node.get('musicResponsiveListItemRenderer')
        if isinstance(renderer, dict):
            node = renderer
        nav = node.get('navigationEndpoint')
        if not isinstance(nav, dict):
            title = node.get('title')
            if isinstance(title, dict) and isinstance(title.get('runs'), list):
                first = next((r for r in title['runs'] if isinstance(r, dict)), None)
                nav = first.get('navigationEndpoint') if isinstance(first, dict) else None
        browse = nav.get('browseEndpoint') if isinstance(nav, dict) else None
        browse_id = browse.get('browseId') if isinstance(browse, dict) else None
        if (not isinstance(browse_id, str)
                or re.fullmatch(r'MPRE[A-Za-z0-9_-]+', browse_id) is None):
            return None
        title_text = None
        title = node.get('title')
        if isinstance(title, dict):
            title_text = text_value(title)
        if title_text is None:
            columns = node.get('flexColumns')
            if isinstance(columns, list) and columns:
                first = columns[0]
                renderer = (first.get('musicResponsiveListItemFlexColumnRenderer')
                           if isinstance(first, dict) else None)
                text = renderer.get('text') if isinstance(renderer, dict) else None
                title_text = text_value(text)
        if title_text is None:
            return None
        return {'browseId': browse_id, 'title': title_text}

    def walk(value):
        nonlocal visited
        if visited >= 20_000 or len(rows) >= 200:
            return
        visited += 1
        if isinstance(value, dict):
            found = candidate(value)
            if found and found['browseId'] not in seen_ids:
                seen_ids.add(found['browseId'])
                rows.append(found)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    return rows


def _sanitized_search_projection(raw_body, content_type):
    candidates = _raw_album_candidates(raw_body, content_type)
    projection = {'album_candidates': candidates}
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode('utf-8')
    return projection, hashlib.sha256(canonical).hexdigest()


def create_observed_paced_session(interval, request_budget, deadline=None,
                                  deadline_clock=None,
                                  search_response_callback=None):
    """Return the normal capped/paced session with path-only endpoint receipts."""
    session = create_paced_session(
        interval, request_budget, deadline=deadline,
        deadline_clock=deadline_clock)
    original_request = session.request
    session.request_events = []
    session.search_response_receipts = []

    def observed_request(*args, **kwargs):
        method, path, url, body = _request_parts(args, kwargs)
        parts = urlsplit(url)
        before_count = session.count
        before_other = session.request_categories.get('other', 0)
        if (method == 'GET' and parts.hostname in {'music.youtube.com', 'www.youtube.com'}
                and path in {'', '/'}):
            category = 'visitor_id_bootstrap'
        else:
            category = _http_request_category(url, body)
        response_record = None
        response_status = None
        response_type = None
        try:
            response = original_request(*args, **kwargs)
            status_code = getattr(response, 'status_code', None)
            content_type = getattr(response, 'headers', {}).get('Content-Type', '')
            response_status = status_code if type(status_code) is int else None
            response_type = str(content_type)[:128]
            response_bytes = getattr(response, 'content', b'')
            if type(response_bytes) is not bytes:
                response_bytes = b''
            if category == 'search_page':
                sanitized_projection, sanitized_sha = _sanitized_search_projection(
                    response_bytes, str(content_type))
                response_record = {
                    'method': method,
                    'endpoint_path_without_query': path,
                    'safe_category': category,
                    'status_code': status_code if type(status_code) is int else None,
                    'response_content_type': str(content_type)[:128],
                    'response_body_bytes': len(response_bytes),
                    'sanitized_response_sha256': sanitized_sha,
                    'sanitized_search_json': sanitized_projection,
                }
                session.search_response_receipts.append(response_record)
                if search_response_callback is not None:
                    search_response_callback(response_record)
            status = 'completed'
            error_type = None
            return response
        except Exception as error:
            status = 'error'
            error_type = type(error).__name__
            failed_response = getattr(error, 'response', None)
            failed_status = getattr(failed_response, 'status_code', None)
            if type(failed_status) is int:
                response_status = failed_status
                response_type = str(getattr(failed_response, 'headers', {}).get(
                    'Content-Type', ''))[:128]
            raise
        finally:
            if session.count == before_count + 1 and category != 'other':
                # The original implementation saw positional[0] ("POST") as
                # the URL. Correct its one count using the real positional URL.
                if session.request_categories.get('other', 0) == before_other + 1:
                    session.request_categories['other'] -= 1
                    if session.request_categories['other'] == 0:
                        del session.request_categories['other']
                    session.request_categories[category] += 1
            session.request_events.append({
                'method': method,
                'path': path,
                'category': category,
                'status': status,
                'error_type': error_type,
                'request_number': session.count if session.count > before_count else None,
                'status_code': response_status,
                'response_content_type': response_type,
                'sanitized_response_sha256': (response_record or {}).get('sanitized_response_sha256'),
            })

    session.request = observed_request
    return session
