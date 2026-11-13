#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "duckdb",
# ]
# ///

import os
from pathlib import Path
import duckdb

# Update this path if your DuckDB file lives elsewhere
DB_PATH = Path("./media/playlist_metadata.duckdb")


def cleanup_and_fix_paths():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = duckdb.connect(str(DB_PATH))

    # Fetch all records
    rows = conn.execute(
        "SELECT id, title, download_path FROM track_metadata"
    ).fetchall()

    fixed_count = 0
    deleted_count = 0

    for track_id, title, path_str in rows:
        if not path_str:
            continue

        path = Path(path_str)

        # 1. Check if the stored file path already exists
        if path.exists():
            continue

        # 2. Check common media extensions (.mp3, .m4a, .mp4, .opus, .webm, .mkv)
        found_path = None
        for ext in [".mp3", ".m4a", ".mp4", ".opus", ".webm", ".mkv"]:
            cand = path.with_suffix(ext)
            if cand.exists():
                found_path = cand
                break

        # 3. Check parent directory for any file with matching stem
        if not found_path and path.parent.exists():
            for sibling in path.parent.glob(f"{path.stem}.*"):
                if sibling.is_file() and sibling.suffix.lower() not in [".part", ".ytdl", ".temp", ".json"]:
                    found_path = sibling
                    break

        if found_path:
            try:
                stored_path = str(found_path.resolve().relative_to(Path.cwd().resolve()))
            except ValueError:
                stored_path = str(found_path.resolve())
            conn.execute(
                """
                UPDATE track_metadata
                SET download_path = ?
                WHERE id = ?
                """,
                [stored_path, track_id],
            )
            print(f"[FIXED EXTENSION] {title}: {path.name} -> {found_path.name}")
            fixed_count += 1
            continue

        # 4. If neither path exists on disk, remove the dangling record
        conn.execute("DELETE FROM track_metadata WHERE id = ?", [track_id])
        # Also clean up any dangling transcript or progress records
        conn.execute("DELETE FROM media_transcripts WHERE track_id = ? OR file_path = ?", [track_id, path_str])
        conn.execute("DELETE FROM media_embeddings WHERE track_id = ? OR file_path = ?", [track_id, path_str])
        conn.execute("DELETE FROM media_progress WHERE file_path = ?", [path_str])
        conn.execute("DELETE FROM media_views WHERE file_path = ?", [path_str])
        print(f"[REMOVED MISSING] {title}: {path.name}")
        deleted_count += 1

    conn.close()
    print(f"\nFinished! Fixed {fixed_count} paths and purged {deleted_count} missing records.")


if __name__ == "__main__":
    cleanup_and_fix_paths()
