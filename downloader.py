#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yt-dlp>=2024.08.01",
#     "duckdb",
# ]
# ///

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
import duckdb
import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "media"
DB_PATH = MEDIA_DIR / "playlist_metadata.duckdb"


def init_db(db_path: Path = DB_PATH):
    """Ensure the DuckDB database and tables exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS track_metadata (
            id VARCHAR PRIMARY KEY,
            title VARCHAR,
            uploader VARCHAR,
            playlist_title VARCHAR,
            playlist_index BIGINT,
            url VARCHAR,
            extractor VARCHAR,
            download_path VARCHAR,
            raw_metadata JSON,
            downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.close()


def get_url_type(url: str) -> str:
    """Classifies URL domain (youtube, soundcloud, or generic)."""
    hostname = urlparse(url).netloc.lower()
    if "youtube.com" in hostname or "youtu.be" in hostname:
        return "youtube"
    if "soundcloud.com" in hostname:
        return "soundcloud"
    return "media"


def resolve_existing_media_file(target_path: Path) -> Path:
    """
    Finds the actual media file on disk even if yt-dlp converted
    the container format (e.g. .m4a -> .mp3, .webm -> .mp4).
    """
    if target_path.exists() and target_path.is_file():
        return target_path

    # Check common audio/video extensions
    for ext in [".mp3", ".m4a", ".mp4", ".opus", ".webm", ".mkv", ".aac", ".ogg"]:
        cand = target_path.with_suffix(ext)
        if cand.exists() and cand.is_file():
            return cand

    # Check for any file with matching stem in the directory
    if target_path.parent.exists():
        for sibling in target_path.parent.glob(f"{target_path.stem}.*"):
            if sibling.is_file() and sibling.suffix.lower() not in [
                ".part",
                ".ytdl",
                ".temp",
                ".json",
                ".orig",
            ]:
                return sibling

    return target_path


def save_track_to_duckdb(info: dict, db_path: Path = DB_PATH) -> str | None:
    """Upserts metadata into DuckDB with the exact verified filepath on disk."""
    raw_filepath = (
        info.get("filepath")
        or info.get("_filename")
        or (info.get("requested_downloads") or [{}])[0].get("filepath")
    )
    if not raw_filepath:
        return None

    actual_file = resolve_existing_media_file(Path(raw_filepath).resolve())

    # Build relative path from project root (e.g. 'media/soundcloud/...')
    try:
        rel_filepath = actual_file.resolve().relative_to(MEDIA_DIR.resolve())
        media_filepath = str(Path("media") / rel_filepath)
    except ValueError:
        try:
            rel_to_base = actual_file.resolve().relative_to(BASE_DIR.resolve())
            media_filepath = str(rel_to_base)
        except ValueError:
            media_filepath = str(actual_file.resolve())

    init_db(db_path)
    track_id = str(info.get("id"))
    title = info.get("title")
    uploader = info.get("uploader")
    playlist_title = info.get("playlist_title") or info.get("album") or "Standalone Media"
    playlist_index = info.get("playlist_index")
    url = info.get("webpage_url") or info.get("url")
    extractor = info.get("extractor")

    # Retry loop in case DuckDB is temporarily locked by web queries
    for attempt in range(5):
        try:
            conn = duckdb.connect(str(db_path))
            conn.execute(
                """
                INSERT INTO track_metadata (
                    id, title, uploader, playlist_title, playlist_index, url, extractor, download_path, raw_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = EXCLUDED.title,
                    uploader = COALESCE(EXCLUDED.uploader, track_metadata.uploader),
                    playlist_title = COALESCE(EXCLUDED.playlist_title, track_metadata.playlist_title),
                    playlist_index = COALESCE(EXCLUDED.playlist_index, track_metadata.playlist_index),
                    download_path = EXCLUDED.download_path,
                    raw_metadata = EXCLUDED.raw_metadata
                """,
                (
                    track_id,
                    title,
                    uploader,
                    playlist_title,
                    playlist_index,
                    url,
                    extractor,
                    media_filepath,
                    json.dumps(info, default=str),
                ),
            )
            conn.close()
            print(f"[DuckDB] Stored/Updated track ({actual_file.suffix}): {title} -> {media_filepath}")
            return media_filepath
        except Exception as e:
            if attempt == 4:
                print(f"[DuckDB Error] Failed to write track {track_id}: {e}", file=sys.stderr)
                raise
            time.sleep(0.1 * (attempt + 1))

    return None


class DuckDBMetadataPP(PostProcessor):
    """
    yt-dlp PostProcessor that runs after all conversion and file movement
    have completed ('after_move' stage). Guarantees that info['filepath']
    points to the actual finished file on disk.
    """

    def __init__(self, downloader=None, db_path: Path = DB_PATH, on_track_saved=None):
        super().__init__(downloader)
        self.db_path = db_path
        self.on_track_saved = on_track_saved

    def run(self, info):
        save_track_to_duckdb(info, db_path=self.db_path)
        if self.on_track_saved:
            self.on_track_saved(info)
        return [], info


def cleanup_extra_playlist_files(
    playlist_title: str,
    playlist_dir: Path | None = None,
    url_type: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    Removes untracked leftover temporary download files for a specific playlist.
    Guarantees that files with stems matching valid tracks are NEVER purged,
    even if the recorded extension differs (e.g. .mp3 vs .m4a).
    """
    if not playlist_title:
        return 0

    conn = duckdb.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT id, download_path 
        FROM track_metadata 
        WHERE playlist_title = ?
        """,
        [playlist_title],
    ).fetchall()
    conn.close()

    if not rows:
        return 0

    # Map of filename stems to their track info
    tracked_stems = {}
    tracked_paths = set()
    for track_id, p_str in rows:
        if p_str:
            p = (BASE_DIR / p_str).resolve()
            tracked_paths.add(p)
            tracked_stems[p.stem] = (track_id, p)

    # Determine playlist directory if not provided
    if playlist_dir is None:
        sample_path = next((p for _, p in tracked_stems.values() if p.parent.exists()), None)
        if sample_path:
            playlist_dir = sample_path.parent
        elif url_type:
            playlist_dir = (MEDIA_DIR / url_type / playlist_title).resolve()
        else:
            playlist_dir = (MEDIA_DIR / "media" / playlist_title).resolve()

    if not playlist_dir.exists():
        return 0

    deleted_count = 0
    updated_records = 0

    for file_path in playlist_dir.glob("*"):
        if not file_path.is_file():
            continue

        # If exact path is tracked, keep it!
        if file_path.resolve() in tracked_paths:
            continue

        # If stem matches a tracked track, keep it and update DB if extension changed!
        if file_path.stem in tracked_stems:
            track_id, recorded_path = tracked_stems[file_path.stem]
            if not recorded_path.exists():
                try:
                    rel_p = file_path.resolve().relative_to(MEDIA_DIR.resolve())
                    new_rel = str(Path("media") / rel_p)
                except ValueError:
                    new_rel = str(file_path.resolve())

                conn = duckdb.connect(str(db_path))
                conn.execute(
                    "UPDATE track_metadata SET download_path = ? WHERE id = ?",
                    [new_rel, track_id],
                )
                conn.close()
                updated_records += 1
                tracked_paths.add(file_path.resolve())
                print(f"[CLEANUP] Re-linked track stem '{file_path.stem}' to {file_path.name}")
            continue

        # Only unlink files that are known temporary files or untracked
        is_temp = any(
            file_path.name.endswith(suffix)
            for suffix in [".part", ".ytdl", ".temp", ".orig", ".orig.m4a", ".orig.mp3"]
        )

        try:
            file_path.unlink()
            deleted_count += 1
            print(f"[CLEANUP] Purged leftover file: {file_path.name} (is_temp={is_temp})")
        except OSError as e:
            print(f"[CLEANUP] Failed to remove {file_path.name}: {e}")

    if deleted_count > 0 or updated_records > 0:
        print(
            f"[CLEANUP] Completed for '{playlist_title}': purged {deleted_count} file(s), re-linked {updated_records} path(s)."
        )

    return deleted_count


def download_media_or_playlist(
    url: str,
    progress_callback=None,
    cancel_check=None,
    db_path: Path = DB_PATH,
) -> dict:
    """
    Downloads media/playlist from YouTube, SoundCloud, or supported URLs.
    Saves metadata safely to DuckDB, prevents purges, and streams progress.
    """
    init_db(db_path)
    url_type = get_url_type(url)

    state = {
        "playlist_title": None,
        "track_count": 0,
        "completed_count": 0,
        "current_title": "",
        "current_percent": 0.0,
        "speed_str": "",
        "eta_seconds": None,
    }

    def ytdl_progress_hook(d):
        if cancel_check and cancel_check():
            raise KeyboardInterrupt("Download cancelled by user")

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            speed = d.get("speed")
            eta = d.get("eta")

            pct = (downloaded / total * 100.0) if total > 0 else 0.0
            state["current_percent"] = round(pct, 1)

            if speed:
                if speed > 1024 * 1024:
                    state["speed_str"] = f"{speed / (1024 * 1024):.1f} MB/s"
                else:
                    state["speed_str"] = f"{speed / 1024:.0f} KB/s"
            else:
                state["speed_str"] = ""

            state["eta_seconds"] = eta

            info = d.get("info_dict") or {}
            if info.get("playlist_title") and not state["playlist_title"]:
                state["playlist_title"] = info["playlist_title"]
            if info.get("n_entries") and not state["track_count"]:
                state["track_count"] = info["n_entries"]
            title = info.get("title") or Path(d.get("filename", "")).stem
            state["current_title"] = title

            if progress_callback:
                progress_callback({
                    "status": "downloading",
                    "playlist_title": state["playlist_title"],
                    "current_track": state["current_title"],
                    "track_index": info.get("playlist_index") or (state["completed_count"] + 1),
                    "total_tracks": state["track_count"],
                    "percent": state["current_percent"],
                    "speed": state["speed_str"],
                    "eta": state["eta_seconds"],
                })

        elif status == "finished":
            state["completed_count"] += 1
            if progress_callback:
                progress_callback({
                    "status": "processing",
                    "playlist_title": state["playlist_title"],
                    "current_track": state["current_title"],
                    "track_index": state["completed_count"],
                    "total_tracks": state["track_count"],
                    "percent": 100.0,
                    "speed": "",
                    "eta": 0,
                })

    ydl_opts = {
        "ignoreerrors": True,
        "no-abort-on-error": True,
        "continuedl": True,
        "embed-thumbnail": True,
        "remote_components": ["ejs:github"],
        "extractor_retries": 15,
        "retry_sleep_functions": {
            "http": lambda n: 15 * (n + 1),
            "extractor": lambda n: 20 * (n + 1),
            "fragment": lambda n: 15 * (n + 1),
            "file_access": lambda n: 10 * (n + 1),
        },
        "sleep_interval": 5,
        "max_sleep_interval": 10,
        "sleep_interval_requests": 2,
        "progress_hooks": [ytdl_progress_hook],
        "cookiesfrombrowser": ("firefox",),
    }

    if url_type == "soundcloud":
        # We download best audio and convert to MP3 for universal browser playback
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegMetadata", "add_metadata": True},
            ],
            "outtmpl": str(
                MEDIA_DIR
                / url_type
                / "%(playlist_title,album,Standalone Media)s"
                / "%(playlist_index,1)03d - %(title)s.%(ext)s"
            ),
        })
    else:
        # YouTube / generic video & audio
        ydl_opts.update({
            "js_runtime": ["deno"],
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True}],
            "outtmpl": str(
                MEDIA_DIR
                / url_type
                / "%(playlist_title,album,Standalone Media)s"
                / "%(playlist_index,1)03d - %(title)s.%(ext)s"
            ),
        })

    def on_track_saved(info):
        pl_title = info.get("playlist_title") or info.get("album")
        if pl_title:
            state["playlist_title"] = pl_title
        if info.get("n_entries"):
            state["track_count"] = info["n_entries"]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Register the DuckDB postprocessor to run AFTER all conversions and file moves
        ydl.add_post_processor(
            DuckDBMetadataPP(ydl, db_path=db_path, on_track_saved=on_track_saved),
            when="after_move",
        )

        # Inspect flat info only (single request) to avoid rate limits
        if progress_callback:
            progress_callback({
                "status": "inspecting",
                "message": "Inspecting URL...",
            })

        try:
            flat_info = ydl.extract_info(url, download=False, process=False)
            if flat_info:
                state["playlist_title"] = flat_info.get("title")
                entries = flat_info.get("entries")
                if entries:
                    state["track_count"] = len(list(entries))
        except Exception:
            pass

        if progress_callback:
            progress_callback({
                "status": "starting",
                "playlist_title": state["playlist_title"] or "Media",
                "total_tracks": state["track_count"] or 1,
            })

        # Execute the download directly without fetching every track twice
        ydl.download([url])

        # Run safe cleanup on the playlist directory
        if state["playlist_title"]:
            cleanup_extra_playlist_files(
                playlist_title=state["playlist_title"],
                url_type=url_type,
                db_path=db_path,
            )

    return {
        "playlist_title": state["playlist_title"],
        "total_tracks": state["track_count"],
        "completed_tracks": state["completed_count"],
        "url_type": url_type,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: downloader.py <URL>")
        sys.exit(1)

    url_arg = sys.argv[1]
    print(f"Starting download for {url_arg}...")
    res = download_media_or_playlist(
        url_arg,
        progress_callback=lambda p: print(f"[{p.get('status')}] {p.get('current_track', '')} {p.get('percent', '')}%"),
    )
    print("Download finished:", res)
