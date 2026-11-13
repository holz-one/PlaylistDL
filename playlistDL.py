#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yt-dlp>=2024.08.01",
#     "duckdb",
# ]
# ///

import argparse
import sys
from pathlib import Path
from downloader import (
    MEDIA_DIR,
    DB_PATH,
    init_db,
    get_url_type,
    download_media_or_playlist,
    cleanup_extra_playlist_files,
    save_track_to_duckdb,
    DuckDBMetadataPP,
)

def main():
    parser = argparse.ArgumentParser(description="Playlist & Media Downloader for Study Hall")
    parser.add_argument("url", help="Media or Playlist URL (YouTube, SoundCloud, etc.)")
    args = parser.parse_args()

    url_type = get_url_type(args.url)
    if not url_type:
        print("Invalid or unsupported URL.", file=sys.stderr)
        sys.exit(1)

    print(f"[{url_type.upper()}] Starting download: {args.url}")

    def on_progress(p):
        status = p.get("status")
        if status == "inspecting":
            print("[INFO] Inspecting URL and metadata...")
        elif status == "starting":
            print(f"[START] Playlist: '{p.get('playlist_title')}' ({p.get('total_tracks')} items)")
        elif status == "downloading":
            track = p.get("current_track", "")
            pct = p.get("percent", 0)
            spd = p.get("speed", "")
            eta = p.get("eta")
            eta_str = f" ETA {eta}s" if eta else ""
            sys.stdout.write(f"\r[DOWNLOADING] {pct:5.1f}% | {spd} | {track[:45]}{eta_str}   ")
            sys.stdout.flush()
        elif status == "processing":
            sys.stdout.write(f"\r[PROCESSING] {p.get('current_track', '')[:50]} (Finalizing)...   \n")
            sys.stdout.flush()

    try:
        summary = download_media_or_playlist(
            url=args.url,
            progress_callback=on_progress,
        )
        print("\n\n" + "=" * 50)
        print(f"Download complete: {summary.get('playlist_title')}")
        print(f"Tracks processed: {summary.get('total_tracks')}")
        print("=" * 50)
    except KeyboardInterrupt:
        print("\n[CANCELLED] Download aborted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] Download failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
