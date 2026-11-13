#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "faster-whisper",
#     "av<14",
#     "duckdb",
#     "requests",
#     "python-dotenv",
# ]
# ///

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv
import duckdb
import requests

load_dotenv()

DB_PATH = Path(os.getenv("DB", "media/playlist_metadata.duckdb"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
CHUNK_DURATION = 45.0  # seconds per semantic chunk for vector embedding


def init_db(con):
    """Ensure required tables exist in DuckDB."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS media_transcripts (
            file_path VARCHAR PRIMARY KEY,
            track_id VARCHAR,
            playlist_title VARCHAR,
            language VARCHAR,
            full_text VARCHAR,
            segments_json JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS media_embeddings (
            id VARCHAR PRIMARY KEY,
            file_path VARCHAR NOT NULL,
            track_id VARCHAR,
            playlist_title VARCHAR,
            chunk_index BIGINT,
            start_seconds DOUBLE,
            end_seconds DOUBLE,
            text VARCHAR,
            embedding FLOAT[768],
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def load_whisper_model(model_size=WHISPER_MODEL, device="auto"):
    """Loads faster-whisper with automatic GPU fallback to CPU."""
    from faster_whisper import WhisperModel
    import ctranslate2
    import numpy as np

    if device in ("auto", "cuda"):
        try:
            print(f"[Whisper] Attempting to load '{model_size}' on CUDA (float16)...")
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            # Probe encode to verify CUDA libraries (e.g. libcublas) actually load
            dummy_feat = np.zeros((1, 80, 3000), dtype=np.float32)
            storage = ctranslate2.StorageView.from_array(dummy_feat)
            model.model.encode(storage)
            print(f"[Whisper] CUDA acceleration verified successfully.")
            return model
        except Exception as e:
            print(f"[Whisper] CUDA acceleration unavailable ({e}). Falling back to CPU...")

    print(f"[Whisper] Loading '{model_size}' on CPU (int8)...")
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def get_embedding(text: str, host: str = OLLAMA_HOST, model: str = OLLAMA_EMBED_MODEL):
    """Fetches a 768-dim vector embedding from Ollama."""
    url = f"{host}/api/embeddings"
    res = requests.post(url, json={"model": model, "prompt": text}, timeout=60)
    res.raise_for_status()
    data = res.json()
    embedding = data.get("embedding")
    if not embedding or len(embedding) != 768:
        raise ValueError(f"Expected 768-dim embedding from {model}, got {len(embedding) if embedding else 0}")
    return embedding


def chunk_segments(segments, target_duration=CHUNK_DURATION):
    """
    Groups Whisper segments into coherent chunks of ~target_duration seconds
    so vector embeddings have sufficient context while maintaining accurate timestamps.
    """
    chunks = []
    current_texts = []
    chunk_start = None
    chunk_end = None

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        if chunk_start is None:
            chunk_start = seg["start"]
        current_texts.append(text)
        chunk_end = seg["end"]

        if (chunk_end - chunk_start) >= target_duration:
            chunks.append({
                "start": round(chunk_start, 2),
                "end": round(chunk_end, 2),
                "text": " ".join(current_texts),
            })
            current_texts = []
            chunk_start = None
            chunk_end = None

    if current_texts and chunk_start is not None:
        chunks.append({
            "start": round(chunk_start, 2),
            "end": round(chunk_end, 2),
            "text": " ".join(current_texts),
        })

    return chunks


def resolve_media_path(raw_path: str) -> Path | None:
    """Finds existing file on disk for a given database path."""
    p = Path(raw_path)
    if p.exists():
        return p.resolve()
    # Check relative to cwd
    cwd_p = Path.cwd() / raw_path
    if cwd_p.exists():
        return cwd_p.resolve()
    # Check with extensions
    for ext in (".mp4", ".m4a", ".mp3", ".webm", ".mkv"):
        alt = p.with_suffix(ext)
        if alt.exists():
            return alt.resolve()
        alt_cwd = (Path.cwd() / raw_path).with_suffix(ext)
        if alt_cwd.exists():
            return alt_cwd.resolve()
    return None


def get_db():
    """Opens a connection to DuckDB with retry for concurrency."""
    for attempt in range(5):
        try:
            return duckdb.connect(str(DB_PATH))
        except duckdb.IOException:
            time.sleep(0.2)
    return duckdb.connect(str(DB_PATH))


def process_track(whisper_model, row, host, embed_model, force=False, embed_only=False):
    """Transcribes a track and generates vector embeddings."""
    track_id, title, playlist_title, raw_path = row
    if not raw_path:
        print(f"[Skip] Track '{title}' has no file path in database.")
        return False

    resolved_path = resolve_media_path(raw_path)
    if not resolved_path:
        print(f"[Skip] File not found on disk for '{title}': {raw_path}")
        return False

    norm_path = raw_path

    # Check if already transcribed
    con = get_db()
    existing = con.execute(
        "SELECT language, full_text, segments_json FROM media_transcripts WHERE file_path = ?",
        [norm_path],
    ).fetchone()

    emb_count = 0
    if existing and not force:
        emb_count = con.execute(
            "SELECT COUNT(*) FROM media_embeddings WHERE file_path = ?",
            [norm_path],
        ).fetchone()[0]
    con.close()

    segments_list = []
    language = "en"
    full_text = ""

    if existing and not force:
        if not embed_only and emb_count > 0:
            print(f"[Cached] '{title}' already transcribed and embedded ({emb_count} chunks). Use --force to redo.")
            return True

        language = existing[0] or "en"
        full_text = existing[1] or ""
        segments_list = json.loads(existing[2]) if existing[2] else []
        print(f"[Transcribed] Loaded cached transcript for '{title}' ({len(segments_list)} segments).")

    elif not embed_only:
        print(f"\n[Transcribing] '{title}' ({resolved_path.name})...")
        t0 = time.time()
        whisper_segments, info = whisper_model.transcribe(
            str(resolved_path),
            beam_size=1,
            language=None,
            vad_filter=True,
        )

        language = info.language
        segments_list = []
        text_parts = []

        for s in whisper_segments:
            seg_dict = {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
            }
            segments_list.append(seg_dict)
            text_parts.append(s.text.strip())

        full_text = " ".join(text_parts)
        dur = time.time() - t0
        print(f"[Transcribed] '{title}' in {dur:.1f}s (language: {language}, {len(segments_list)} segments)")

        # Save to media_transcripts
        con = get_db()
        con.execute(
            """
            INSERT INTO media_transcripts (file_path, track_id, playlist_title, language, full_text, segments_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (file_path) DO UPDATE SET
                track_id = EXCLUDED.track_id,
                playlist_title = EXCLUDED.playlist_title,
                language = EXCLUDED.language,
                full_text = EXCLUDED.full_text,
                segments_json = EXCLUDED.segments_json,
                created_at = now()
            """,
            [norm_path, track_id, playlist_title, language, full_text, json.dumps(segments_list)],
        )
        con.close()

    # Generate semantic chunks and vector embeddings
    chunks = chunk_segments(segments_list, target_duration=CHUNK_DURATION)
    if not chunks and full_text:
        chunks = [{"start": 0.0, "end": 0.0, "text": full_text[:500]}]

    print(f"[Embedding] Generating Ollama embeddings for {len(chunks)} chunks using {embed_model}...")
    con = get_db()
    con.execute("DELETE FROM media_embeddings WHERE file_path = ?", [norm_path])

    embedded_count = 0
    for idx, chunk in enumerate(chunks):
        try:
            emb = get_embedding(chunk["text"], host=host, model=embed_model)
            chunk_id = f"{track_id or 'tr'}_{idx}_{uuid.uuid4().hex[:6]}"
            con.execute(
                """
                INSERT INTO media_embeddings (id, file_path, track_id, playlist_title, chunk_index, start_seconds, end_seconds, text, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())
                """,
                [
                    chunk_id,
                    norm_path,
                    track_id,
                    playlist_title,
                    idx,
                    chunk["start"],
                    chunk["end"],
                    chunk["text"],
                    emb,
                ],
            )
            embedded_count += 1
        except Exception as e:
            print(f"  [Warning] Failed to embed chunk {idx} for '{title}': {e}")

    con.close()
    print(f"[Done] Stored {embedded_count}/{len(chunks)} vector embeddings for '{title}'.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio/video tracks using Whisper and store vector embeddings using Ollama."
    )
    parser.add_argument("--playlist", help="Filter by specific playlist title")
    parser.add_argument("--file", help="Filter by specific media file path")
    parser.add_argument("--model", default=WHISPER_MODEL, help=f"Whisper model size (default: {WHISPER_MODEL})")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="Compute device (auto/cuda/cpu)")
    parser.add_argument("--ollama-host", default=OLLAMA_HOST, help=f"Ollama server URL (default: {OLLAMA_HOST})")
    parser.add_argument("--embed-model", default=OLLAMA_EMBED_MODEL, help=f"Ollama embedding model (default: {OLLAMA_EMBED_MODEL})")
    parser.add_argument("--force", action="store_true", help="Re-transcribe and re-embed even if already present")
    parser.add_argument("--embed-only", action="store_true", help="Only recompute embeddings from existing transcripts")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    # Verify Ollama server
    try:
        r = requests.get(f"{args.ollama_host}/api/tags", timeout=5)
        r.raise_for_status()
        installed_models = [m["name"] for m in r.json().get("models", [])]
        print(f"[Ollama] Connected to {args.ollama_host}. Installed models: {', '.join(installed_models)}")
        # Check embed model prefix
        embed_found = any(m == args.embed_model or m.startswith(f"{args.embed_model}:") for m in installed_models)
        if not embed_found:
            print(f"[Ollama Warning] Model '{args.embed_model}' not found in installed models! Available: {installed_models}")
    except Exception as e:
        print(f"[Ollama Error] Could not connect to Ollama at {args.ollama_host}: {e}")
        print("Please ensure Ollama is running (`ollama serve`).")
        sys.exit(1)

    con = get_db()
    init_db(con)

    query = "SELECT id, title, playlist_title, download_path FROM track_metadata WHERE download_path IS NOT NULL"
    params = []

    if args.playlist:
        query += " AND playlist_title = ?"
        params.append(args.playlist)

    if args.file:
        query += " AND download_path = ?"
        params.append(args.file)

    query += " ORDER BY playlist_title ASC, playlist_index ASC NULLS LAST"
    tracks = con.execute(query, params).fetchall()
    con.close()

    if not tracks:
        print("No matching tracks found in database.")
        return

    print(f"\nFound {len(tracks)} track(s) matching criteria.")

    whisper_model = None
    if not args.embed_only:
        whisper_model = load_whisper_model(model_size=args.model, device=args.device)

    success_count = 0
    for idx, track in enumerate(tracks, start=1):
        print(f"\n[{idx}/{len(tracks)}] Processing: {track[1] or 'Untitled'}")
        ok = process_track(
            whisper_model=whisper_model,
            row=track,
            host=args.ollama_host,
            embed_model=args.embed_model,
            force=args.force,
            embed_only=args.embed_only,
        )
        if ok:
            success_count += 1

    print(f"\n==========================================")
    print(f"Completed: {success_count}/{len(tracks)} track(s) successfully processed.")
    print(f"==========================================")


if __name__ == "__main__":
    main()
