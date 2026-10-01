"""Collect one explicitly selected YTMusic seed as read-only research evidence."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from inspect_music_library import decode_musicdb, parse_tracks


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def clean_track(track):
    # Avoid storing feedback tokens, account flags, cookies, or request headers.
    return {
        "video_id": track.get("videoId"),
        "title": track.get("title"),
        "artists": [{"name": a.get("name"), "id": a.get("id")}
                    for a in (track.get("artists") or [])],
        "album": track.get("album"),
        "duration_seconds": track.get("duration_seconds"),
        "length": track.get("length"),
        "video_type": track.get("videoType"),
    }


def local_candidates(track, local_tracks):
    """Return candidates, never claim a confirmed recording identity."""
    title = normalize(track.get("title") or "")
    artists = {normalize(a.get("name") or "") for a in (track.get("artists") or [])}
    artists.discard("")
    if not title or not artists:
        return []
    duration = track.get("duration_seconds")
    if duration is None and isinstance(track.get("length"), str):
        parts = track["length"].split(":")
        if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
            duration = 0
            for part in parts:
                duration = duration * 60 + int(part)
    return [t["persistent_id"] for t in local_tracks
            if normalize(t.get("title") or "") == title
            and normalize(t.get("artist") or "") in artists
            and (duration is None or t.get("duration_ms") is None
                 or abs(t["duration_ms"] / 1000 - duration) <= 3)]


def observe(rows, relation, timestamp, local_tracks, seed_video_id, section=None):
    observations = []
    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not row.get("videoId"):
            continue
        observations.append({
            "source": "ytmusic", "relation": relation, "section": section,
            "position": position, "observed_at": timestamp,
            "is_seed": row["videoId"] == seed_video_id,
            "track": clean_track(row),
            "local_metadata_candidates": local_candidates(row, local_tracks),
            "matching_method": "exact_title_artist_and_duration_within_3s_if_available",
            "identity_status": "unverified", "genius_rank": None,
        })
    return observations


def collect(client, local_seed, local_tracks, video_id, limit):
    snapshot = {
        "schema_version": 1, "source_version": version("ytmusicapi"),
        "session_id": str(uuid4()),
        "local_seed": local_seed, "selected_video_id": video_id,
        "seed_identity_status": "manually_selected_candidate",
        "account_authentication_verified": False,
        "observations": [], "requests": [],
    }
    radio_time = datetime.now(timezone.utc).isoformat()
    radio = client.get_watch_playlist(videoId=video_id, radio=True, limit=limit)
    snapshot["requests"].append({"endpoint": "watch_radio", "observed_at": radio_time,
                                 "returned_track_count": len(radio.get("tracks") or [])})
    snapshot["observations"].extend(observe(
        radio.get("tracks") or [], "radio", radio_time, local_tracks, video_id))
    # Related is obtained from a separate seed watch response; it need not exist.
    watch_time = datetime.now(timezone.utc).isoformat()
    watch = client.get_watch_playlist(videoId=video_id, radio=False, limit=limit)
    related_id = watch.get("related")
    snapshot["requests"].append({"endpoint": "watch_seed", "observed_at": watch_time,
                                 "related_available": bool(related_id)})
    if related_id:
        related_time = datetime.now(timezone.utc).isoformat()
        sections = client.get_song_related(related_id)
        snapshot["requests"].append({"endpoint": "song_related", "observed_at": related_time,
                                     "returned_section_count": len(sections)})
        for section in sections:
            rows = section.get("contents")
            if isinstance(rows, list):
                snapshot["observations"].extend(observe(
                    rows, "related", related_time, local_tracks, video_id,
                    section=section.get("title")))
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--auth", type=Path, default=Path("../browser.json"))
    parser.add_argument("--seed-title", required=True)
    parser.add_argument("--video-id", required=True,
                        help="Explicitly selected candidate; not automatically matched")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", args.video_id):
        parser.error("Expected an 11-character video ID")
    if not 1 <= args.limit <= 100:
        parser.error("limit must be between 1 and 100")
    # Never write inside an input library or overwrite an existing file.
    output = args.output.resolve()
    bundle = args.bundle.resolve()
    if output.is_relative_to(bundle) or output == args.auth.resolve():
        parser.error("output must be outside the input library and auth file")
    if output.exists():
        parser.error("output already exists; choose a new observation filename")
    try:
        import requests
        from ytmusicapi import YTMusic

        class TimedSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs.setdefault("timeout", 25)
                return super().request(*args, **kwargs)

        tracks, _ = parse_tracks(decode_musicdb((bundle / "Library.musicdb").read_bytes()))
        seeds = [t for t in tracks if t.get("title") == args.seed_title]
        if len(seeds) != 1:
            raise ValueError("Seed title must identify exactly one local record")
        client = YTMusic(str(args.auth), requests_session=TimedSession(),
                         language="en", location="JP")
        snapshot = collect(client, seeds[0], tracks, args.video_id, args.limit)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        observations = snapshot["observations"]
        print(json.dumps({
            "output": str(output), "observations": len(observations),
            "non_seed_observations": sum(not o["is_seed"] for o in observations),
            "non_seed_with_local_candidates": sum(
                not o["is_seed"] and bool(o["local_metadata_candidates"])
                for o in observations),
            "relations": sorted({o["relation"] for o in observations}),
        }))
    except Exception as error:
        # HTTP/auth exceptions can contain sensitive headers. Show only the type.
        parser.exit(1, f"YTMusic probe failed ({type(error).__name__}); no credentials displayed.\n")


if __name__ == "__main__":
    main()
