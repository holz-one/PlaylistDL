#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yt-dlp>=2024.08.01",  # Pin to recent or unpinned to allow updates
#     "duckdb",
# ]
# ///

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
import duckdb
import yt_dlp
from yt_dlp.utils import DownloadError

MEDIA_DIR = Path("./media")
#COOKIES_DIR = Path("../")
DB_PATH = MEDIA_DIR / "playlist_metadata.duckdb"

def init_db(db_path: Path):
    """Ensure the DuckDB database and tables exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute("""
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
    """)
    conn.close()

def save_to_duckdb_hook(d):
    """yt-dlp hook executed when a track finishes downloading."""
    if d['status'] == 'finished':
        info = d.get('info_dict', {})
        media_filepath = str(Path(d['filename']).resolve())
        
        # Connect to DuckDB and upsert track metadata
        conn = duckdb.connect(str(DB_PATH))
        
        # Insert metadata, updating on primary key conflict (track ID)
        conn.execute("""
            INSERT INTO track_metadata (
                id, title, uploader, playlist_title, playlist_index, url, extractor, download_path, raw_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = EXCLUDED.title,
                download_path = EXCLUDED.download_path,
                raw_metadata = EXCLUDED.raw_metadata
        """, (
            str(info.get('id')),
            info.get('title'),
            info.get('uploader'),
            info.get('playlist_title'),
            info.get('playlist_index'),
            info.get('webpage_url'),
            info.get('extractor'),
            media_filepath,
            json.dumps(info, default=str)  # Dump full metadata as a JSON string
        ))
        conn.close()
        print(f"Stored metadata in DuckDB for: {info.get('title')}")

# Initialize database schema
init_db(DB_PATH)

def get_url_type(url):
    hostname = urlparse(url).netloc.lower()
    if "youtube.com" in hostname or "youtu.be" in hostname:
        return "youtube"
    if "soundcloud.com" in hostname:
        return "soundcloud"
    return None

parser = argparse.ArgumentParser()
parser.add_argument("url", help="Media or Playlist URL")
args = parser.parse_args()

url_type = get_url_type(args.url)
if not url_type:
    print("Invalid or unsupported URL.")
    exit(1)

#cookies_path = COOKIES_DIR / ("soundcloud.com_cookies.txt" if url_type == "soundcloud" else "www.youtube.com_cookies.txt")

#if not cookies_path.exists():
#    print(f"Cookies file not found at {cookies_path}.")
#    exit(1)

ydl_opts = {
    #"cookiefile": str(cookies_path),
    "ignoreerrors": True,
    "no-abort-on-error": True,
    "continuedl": True,
    "embed-thumbnail": True,
    # Enable downloading the required challenge solver components
    "remote_components": ["ejs:github"],
    "extractor_retries": 10,
    "retry_sleep_functions": {"http": lambda n: 15 * n},
    "sleep_interval": 5,
    "max_sleep_interval": 10,
    "progress_hooks": [save_to_duckdb_hook],
    # Extract directly from a local browser:
    "cookiesfrombrowser": ("firefox",),
}

if url_type == "soundcloud":
    ydl_opts.update({
        "format": "bestaudio/best",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "outtmpl": str(MEDIA_DIR / url_type / "%(playlist_title)s" / "%(playlist_index)03d - %(title)s.%(ext)s"),
    })
else:
    ydl_opts.update({
        "js_runtime": ["deno"],
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True}],
        "outtmpl": str(MEDIA_DIR / url_type / "%(playlist_title)s" / "%(playlist_index)03d - %(title)s.%(ext)s"),
    })

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([args.url])
except DownloadError as e:
    print(f"Download Failed: {e}")
