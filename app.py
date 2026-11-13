# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "flask",
#     "flask_login",
#     "werkzeug",
#     "duckdb",
#     "feedparser",
#     "python-dotenv",
#     "pyyaml",
#     "requests",
#     "lxml",
#     "SalamCast",
#     "yt-dlp>=2024.08.01",
# ]
# ///

import io
import json
import math
import mimetypes
import os
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import duckdb
import feedparser
import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from salamcast import SalamCastGen
import downloader

load_dotenv()

TITLE = os.getenv("TITLE", "My Podcast Server")
EMAIL = os.getenv("EMAIL", "webmaster@mysite.com")
FILES_DB = os.getenv("DB", "media/playlist_metadata.duckdb")
FILE_DIR = os.getenv("FILE_DIR", "media/")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

app = Flask(__name__)
app.config["SERVER_ADMIN"] = EMAIL

FEEDS = {}
_db_lock = threading.RLock()


def get_db():
    return duckdb.connect(FILES_DB)


@contextmanager
def db_connection():
    """Serialize DuckDB access in-process and ensure connections always close."""
    with _db_lock:
        conn = get_db()
        try:
            yield conn
        finally:
            conn.close()


def init_db():
    """Ensure required DuckDB tables exist."""
    Path(FILES_DB).parent.mkdir(parents=True, exist_ok=True)
    with db_connection() as conn:
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_progress (
                file_path VARCHAR PRIMARY KEY,
                last_position_seconds DOUBLE DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_views (
                file_path VARCHAR PRIMARY KEY,
                views_count BIGINT DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_notes (
                note_id VARCHAR PRIMARY KEY,
                file_path VARCHAR NOT NULL,
                timestamp_seconds DOUBLE NOT NULL,
                content VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
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
        conn.execute(
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


init_db()


class DownloadTask:
    def __init__(self, task_id: str, url: str, auto_transcribe: bool = False):
        self.task_id = task_id
        self.url = url
        self.auto_transcribe = auto_transcribe
        self.url_type = ""
        self.status = "queued"  # queued, inspecting, downloading, transcribing, completed, error, cancelled
        self.playlist_title = ""
        self.current_track = ""
        self.track_index = 0
        self.total_tracks = 0
        self.percent = 0.0
        self.speed = ""
        self.eta = None
        self.error_message = None
        self.created_at = time.time()
        self.updated_at = time.time()
        self.completed_at = None
        self._cancelled = False

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "url": self.url,
            "url_type": self.url_type,
            "status": self.status,
            "playlist_title": self.playlist_title,
            "current_track": self.current_track,
            "track_index": self.track_index,
            "total_tracks": self.total_tracks,
            "percent": self.percent,
            "speed": self.speed,
            "eta": self.eta,
            "error_message": self.error_message,
            "auto_transcribe": self.auto_transcribe,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


class DownloadTaskManager:
    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        self.lock = threading.Lock()

    def start_download(self, url: str, auto_transcribe: bool = False) -> str:
        task_id = str(uuid.uuid4())
        task = DownloadTask(task_id, url, auto_transcribe=auto_transcribe)
        with self.lock:
            self.tasks[task_id] = task

        thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        thread.start()
        return task_id

    def cancel_task(self, task_id: str) -> bool:
        with self.lock:
            task = self.tasks.get(task_id)
            if task and task.status in ["queued", "inspecting", "downloading", "transcribing"]:
                task._cancelled = True
                task.status = "cancelled"
                task.updated_at = time.time()
                return True
        return False

    def get_task(self, task_id: str) -> dict | None:
        with self.lock:
            task = self.tasks.get(task_id)
            return task.to_dict() if task else None

    def get_all_tasks(self, limit: int = 20) -> list[dict]:
        with self.lock:
            sorted_tasks = sorted(
                self.tasks.values(), key=lambda t: t.created_at, reverse=True
            )
            return [t.to_dict() for t in sorted_tasks[:limit]]

    def get_active_count(self) -> int:
        with self.lock:
            return sum(
                1 for t in self.tasks.values()
                if t.status in ["queued", "inspecting", "downloading", "transcribing"]
            )

    def _run_task(self, task: DownloadTask):
        try:
            task.url_type = downloader.get_url_type(task.url)
            task.status = "inspecting"
            task.updated_at = time.time()

            def progress_cb(p):
                with self.lock:
                    if task._cancelled:
                        return
                    task.updated_at = time.time()
                    st = p.get("status")
                    if st == "inspecting":
                        task.status = "inspecting"
                    elif st == "starting":
                        task.status = "downloading"
                        if p.get("playlist_title"):
                            task.playlist_title = p.get("playlist_title")
                        if p.get("total_tracks"):
                            task.total_tracks = p.get("total_tracks")
                    elif st == "downloading":
                        task.status = "downloading"
                        task.current_track = p.get("current_track", "")
                        task.track_index = p.get("track_index", 0)
                        task.total_tracks = p.get("total_tracks", 0)
                        task.percent = p.get("percent", 0.0)
                        task.speed = p.get("speed", "")
                        task.eta = p.get("eta")
                        if p.get("playlist_title"):
                            task.playlist_title = p.get("playlist_title")
                    elif st == "processing":
                        task.status = "downloading"
                        task.current_track = p.get("current_track", "")
                        task.track_index = p.get("track_index", 0)

            def cancel_check():
                return task._cancelled

            res = downloader.download_media_or_playlist(
                url=task.url,
                progress_callback=progress_cb,
                cancel_check=cancel_check,
                db_path=Path(FILES_DB),
            )

            task.playlist_title = res.get("playlist_title") or task.playlist_title
            task.total_tracks = res.get("total_tracks") or task.total_tracks

            if task.auto_transcribe and not task._cancelled and task.playlist_title:
                task.status = "transcribing"
                task.percent = 0.0
                task.speed = "Whisper AI"
                task.updated_at = time.time()
                try:
                    cmd = ["uv", "run", "transcribe.py", "--playlist", task.playlist_title]
                    sub_proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if sub_proc.returncode != 0:
                        app.logger.warning(f"Auto-transcribe failed for {task.playlist_title}: {sub_proc.stderr}")
                except Exception as ex:
                    app.logger.warning(f"Auto-transcribe exception: {ex}")

            task.status = "completed"
            task.percent = 100.0
            task.completed_at = time.time()
            task.updated_at = time.time()

        except KeyboardInterrupt:
            task.status = "cancelled"
            task.updated_at = time.time()
        except Exception as e:
            task.status = "error"
            task.error_message = str(e)
            task.updated_at = time.time()
            app.logger.exception(f"Download task {task.task_id} failed: {e}")


download_manager = DownloadTaskManager()


def get_playlists_feeds():
    """Groups distinct playlists dynamically by their uploader/group."""
    playlist_files = []
    feeds_dict = {}

    if not os.path.exists(FILES_DB):
        return playlist_files, feeds_dict

    try:
        with db_connection() as con:
            rows = con.execute(
                """
                SELECT DISTINCT playlist_title, uploader  
                FROM track_metadata 
                WHERE playlist_title IS NOT NULL  
                ORDER BY uploader ASC, playlist_title ASC
                """
            ).fetchall()

        # Fixed: Used defaultdict to prevent KeyError on missing keys
        grouped_dirs = defaultdict(list)
        for playlist, parent_group in rows:
            if not playlist:
                continue

            group_name = parent_group or "Unknown Group"
            filename = f"{playlist}.xml"
            feed_url = f"/media/playlists/local/{quote(filename, safe='')}"
            feeds_dict[filename] = feed_url

            grouped_dirs[group_name].append(
                {
                    "xml": filename,
                    "raw_name": f"{group_name} - {playlist}",
                    "label": playlist,
                }
            )

        # Format into the template's expected layout structure
        for group_name, feed_items in grouped_dirs.items():
            playlist_files.append(
                {
                    "tag": "playlist",
                    "label": group_name,
                    "feed_items": feed_items,
                }
            )

    except Exception as e:
        app.logger.error(f"Error building directory tree: {e}")

    return playlist_files, feeds_dict


def make_playlist_feed(playlist, email="podcast@podcast.srv"):
    """Generates an RSS feed using SalamCastGen for a given playlist from DuckDB."""
    playlist = unquote(playlist)

    if not os.path.exists(FILES_DB):
        return Response("Database file not found", status=404)

    with db_connection() as con:
        rows = con.execute(
            """
            SELECT
                id, 
                title, 
                playlist_title as playlist,
                raw_metadata as description, 
                uploader as author,
                download_path as file_path, 
                downloaded_at as created_time, 
                downloaded_at as modified_time
            FROM track_metadata 
            WHERE playlist_title = ? 
            ORDER BY playlist_index ASC NULLS LAST, file_path ASC, title ASC 
        """,
            [playlist],
        ).fetchall()

    if not rows:
        feed = SalamCastGen()
        return feed.error_feed("404")

    feed = SalamCastGen()
    feed.enable_itunes()
    feed.set_title(playlist)
    feed.set_description(f"Podcast feed for {playlist}")
    feed.set_owner(email, email)

    for r in rows:
        (
            id,
            title,
            playlist,
            description,
            author,
            file_path,
            created_time,
            modified_time,
        ) = r
        item_title = (
            title
            if title
            else (
                os.path.basename(file_path) if file_path else "Untitled Episode"
            )
        )

        episode = feed.create_new_item()
        episode.id = id
        episode.set_title(item_title)

        pub_date_str = (
            str(created_time)
            if created_time
            else (str(modified_time) if modified_time else "")
        )

        desc_parts = []
        if description:
            desc_parts.append(str(description))

        episode.set_description(
            "".join(desc_parts) if desc_parts else item_title
        )
        episode.set_author(author or "")

        full_media_path = file_path if file_path else ""
        if full_media_path:
            episode.add_element("file_path", full_media_path)

        if full_media_path and os.path.exists(full_media_path):
            file_size = str(os.path.getsize(full_media_path))
        else:
            file_size = "4096"

        # Normalize absolute paths under FILE_DIR to /media/... URLs
        url_rel_path = full_media_path
        if full_media_path:
            try:
                rel_to_media = Path(full_media_path).resolve().relative_to(Path(FILE_DIR).resolve())
                url_rel_path = f"media/{rel_to_media.as_posix()}"
            except ValueError:
                url_rel_path = full_media_path.lstrip("/")

        encoded_rel_path = quote(url_rel_path, safe="/:")
        media_url = request.url_root.rstrip("/") + "/" + encoded_rel_path

        guessed_mime, _ = mimetypes.guess_type(full_media_path)
        final_mime = guessed_mime or "video/mp4"

        episode.set_enclosure(media_url, file_size, final_mime)

        if pub_date_str:
            episode.set_date(pub_date_str)

        feed.add_item(episode)

    return feed.generate_feed()

@app.route("/")
def index():
    # Load initial feeds and playlists
    playlist_files, pl_feeds = get_playlists_feeds()
    FEEDS.update(pl_feeds)

    return render_template(
        "player.html",
        title=TITLE,
        tag="avfiles",
        Files=playlist_files,
    )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico")


@app.route("/DLplaylist.png")
def study_hall_banner():
    return send_from_directory(".", "DLplaylist.png")

@app.route("/fonts/<path:filename>")
def fonts(filename):
    return send_from_directory("fonts", filename)


@app.route("/media/<path:filename>")
def media(filename):
    return send_from_directory(FILE_DIR, filename)


@app.route("/media/playlists/<service>/<path:playlist_file>")
def get_playlist(service, playlist_file):
    playlist_name = playlist_file.removesuffix(".xml")
    return make_playlist_feed(playlist_name, app.config["SERVER_ADMIN"])

@app.route("/api/feed/<path:feed_id>")
def get_feed(feed_id):
    # Load initial feeds and playlists
    _, pl_feeds = get_playlists_feeds()
    FEEDS.update(pl_feeds)
    decoded_feed_id = unquote(feed_id)
    feed_url = FEEDS.get(decoded_feed_id)

    if not feed_url:
        return jsonify({"error": f"Feed '{decoded_feed_id}' not found"}), 404

    if feed_url.startswith("http://") or feed_url.startswith("https://"):
        target_url = feed_url
        parsed = feedparser.parse(target_url)
    else:
        target_url = request.url_root.rstrip("/") + feed_url
        playlist_name = decoded_feed_id.removesuffix(".xml")
        feed_response = make_playlist_feed(playlist_name, app.config["SERVER_ADMIN"])
        if feed_response.status_code != 200:
            return jsonify({"error": f"Failed to load feed '{decoded_feed_id}'"}), feed_response.status_code
        parsed = feedparser.parse(feed_response.get_data(as_text=True))

    items = []
    for entry in parsed.entries:
        enclosure = entry.enclosures[0] if entry.get("enclosures") else None
        items.append({
            "title": entry.get("title"),
            "media_url": enclosure.href if enclosure else None,
            "media_type": enclosure.type if enclosure else None,
            "description": entry.get("summary", "") or entry.get("description", ""),
            "pubDate": entry.get("published", "") or entry.get("updated", ""),
            # Raw DB fields for the edit modal:
            "file_path": entry.get("file_path", ""),
            "playlist": decoded_feed_id,
            "transcript": entry.get("transcript", "")
        })

    return jsonify({
        "title": parsed.feed.get("title", "Untitled Feed"),
        "description": parsed.feed.get("description", ""),
        "link": target_url,
        "items": items,
    })

@app.route('/api/media/state', methods=['GET'])
def get_media_state():
    file_path = request.args.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    with db_connection() as conn:
        state = conn.execute(
            """
            SELECT
                COALESCE((SELECT last_position_seconds FROM media_progress WHERE file_path = ?), 0.0) AS resume_position,
                COALESCE((SELECT views_count FROM media_views WHERE file_path = ?), 0) AS views
            """,
            [file_path, file_path],
        ).fetchone()

        notes_res = conn.execute(
            """
            SELECT note_id, timestamp_seconds, content
            FROM media_notes
            WHERE file_path = ?
            ORDER BY timestamp_seconds ASC, created_at ASC
            """,
            [file_path],
        ).fetchall()

    return jsonify({
        "resume_position": float(state[0]) if state else 0.0,
        "views": int(state[1]) if state else 0,
        "notes": [{"id": n[0], "timestamp": float(n[1]), "content": n[2]} for n in notes_res],
    })

@app.route('/api/media/save-position', methods=['POST'])
def save_position():
    data = request.get_json(silent=True) or {}
    file_path = data.get('file_path')

    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    try:
        position = float(data.get('position', 0.0))
        if not math.isfinite(position) or position < 0:
            position = 0.0
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid position"}), 400

    for _ in range(3):
        try:
            with db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO media_progress (file_path, last_position_seconds, updated_at)
                    VALUES (?, ?, now())
                    ON CONFLICT(file_path) DO UPDATE SET
                        last_position_seconds = EXCLUDED.last_position_seconds,
                        updated_at = now()
                    """,
                    [file_path, position],
                )
            return jsonify({"status": "success"})
        except duckdb.TransactionException:
            time.sleep(0.05)

    return jsonify({"error": "Database busy"}), 500


@app.route('/api/media/view', methods=['POST'])
def increment_view():
    data = request.get_json(silent=True) or {}
    file_path = data.get('file_path')

    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    for _ in range(3):
        try:
            with db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO media_views (file_path, views_count)
                    VALUES (?, 1)
                    ON CONFLICT(file_path) DO UPDATE SET
                        views_count = views_count + 1
                    """,
                    [file_path],
                )
                row = conn.execute(
                    "SELECT views_count FROM media_views WHERE file_path = ?",
                    [file_path],
                ).fetchone()
            return jsonify({"status": "success", "views": int(row[0]) if row else 1})
        except duckdb.TransactionException:
            time.sleep(0.05)

    return jsonify({"error": "Database busy"}), 500


@app.route('/api/media/notes', methods=['POST'])
def add_note():
    data = request.get_json(silent=True) or {}
    file_path = data.get('file_path')
    content = str(data.get('content', '')).strip()

    if not file_path or not content:
        return jsonify({"error": "file_path and content are required"}), 400

    try:
        timestamp = float(data.get('timestamp', 0.0))
        if not math.isfinite(timestamp) or timestamp < 0:
            timestamp = 0.0
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid timestamp"}), 400

    note_id = str(uuid.uuid4())
    for _ in range(3):
        try:
            with db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO media_notes (note_id, file_path, timestamp_seconds, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    [note_id, file_path, timestamp, content],
                )
            return jsonify({
                "status": "created",
                "note_id": note_id,
                "note": {
                    "id": note_id,
                    "timestamp": timestamp,
                    "content": content,
                },
            }), 201
        except duckdb.TransactionException:
            time.sleep(0.05)

    return jsonify({"error": "Database busy"}), 500


@app.route('/api/media/notes/<note_id>', methods=['DELETE'])
def delete_note(note_id):
    if not note_id:
        return jsonify({"error": "note_id is required"}), 400

    for _ in range(3):
        try:
            with db_connection() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM media_notes WHERE note_id = ?",
                    [note_id],
                ).fetchone()
                if not existing:
                    return jsonify({"error": "Note not found"}), 404
                conn.execute("DELETE FROM media_notes WHERE note_id = ?", [note_id])
            return jsonify({"status": "deleted"})
        except duckdb.TransactionException:
            time.sleep(0.05)

    return jsonify({"error": "Database busy"}), 500


def format_seconds(seconds):
    try:
        total = max(0, int(round(float(seconds))))
        hrs = total // 3600
        mins = (total % 3600) // 60
        secs = total % 60
        if hrs > 0:
            return f"{hrs}:{mins:02d}:{secs:02d}"
        return f"{mins}:{secs:02d}"
    except (TypeError, ValueError):
        return "0:00"


def get_query_embedding(text):
    res = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    res.raise_for_status()
    data = res.json()
    emb = data.get("embedding")
    if not emb or len(emb) != 768:
        raise ValueError(f"Expected 768-dim embedding, got {len(emb) if emb else 0}")
    return emb


@app.route('/api/media/transcript', methods=['GET'])
def get_transcript():
    file_path = request.args.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT track_id, playlist_title, language, full_text, segments_json
            FROM media_transcripts
            WHERE file_path = ?
            """,
            [file_path],
        ).fetchone()

    if not row:
        return jsonify({"found": False, "message": "No transcript generated yet for this track."}), 404

    segments = json.loads(row[4]) if row[4] else []
    return jsonify({
        "found": True,
        "track_id": row[0],
        "playlist_title": row[1],
        "language": row[2],
        "full_text": row[3],
        "segments": segments,
    })


@app.route('/api/playlist/search', methods=['POST'])
def search_playlist():
    data = request.get_json(silent=True) or {}
    query = str(data.get('query', '')).strip()
    playlist = data.get('playlist')
    limit = min(max(int(data.get('limit', 10)), 1), 50)

    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        query_vec = get_query_embedding(query)
    except Exception as e:
        return jsonify({"error": f"Failed to generate embedding from Ollama: {e}"}), 502

    with db_connection() as conn:
        if playlist:
            playlist_name = unquote(playlist).removesuffix(".xml")
            rows = conn.execute(
                """
                SELECT
                    e.id,
                    e.file_path,
                    e.track_id,
                    e.playlist_title,
                    COALESCE(t.title, e.file_path) AS track_title,
                    e.start_seconds,
                    e.end_seconds,
                    e.text,
                    array_cosine_similarity(e.embedding, ?::FLOAT[768]) AS similarity
                FROM media_embeddings e
                LEFT JOIN track_metadata t ON (e.file_path = t.download_path OR e.track_id = t.id)
                WHERE e.playlist_title = ?
                ORDER BY similarity DESC
                LIMIT ?
                """,
                [query_vec, playlist_name, limit],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    e.id,
                    e.file_path,
                    e.track_id,
                    e.playlist_title,
                    COALESCE(t.title, e.file_path) AS track_title,
                    e.start_seconds,
                    e.end_seconds,
                    e.text,
                    array_cosine_similarity(e.embedding, ?::FLOAT[768]) AS similarity
                FROM media_embeddings e
                LEFT JOIN track_metadata t ON (e.file_path = t.download_path OR e.track_id = t.id)
                ORDER BY similarity DESC
                LIMIT ?
                """,
                [query_vec, limit],
            ).fetchall()

    results = []
    for r in rows:
        results.append({
            "id": r[0],
            "file_path": r[1],
            "track_id": r[2],
            "playlist_title": r[3],
            "track_title": r[4],
            "start_seconds": float(r[5]),
            "end_seconds": float(r[6]),
            "formatted_time": format_seconds(r[5]),
            "text": r[7],
            "similarity": round(float(r[8]), 4),
        })

    return jsonify({"query": query, "results": results})


@app.route('/api/playlist/chat', methods=['POST'])
def chat_playlist():
    data = request.get_json(silent=True) or {}
    message = str(data.get('message', '')).strip()
    playlist = data.get('playlist')
    history = data.get('history', [])

    if not message:
        return jsonify({"error": "message is required"}), 400

    playlist_name = unquote(playlist).removesuffix(".xml") if playlist else "All Playlists"

    try:
        query_vec = get_query_embedding(message)
    except Exception as e:
        return jsonify({"error": f"Failed to connect to Ollama embedding service: {e}"}), 502

    with db_connection() as conn:
        if playlist:
            rows = conn.execute(
                """
                SELECT
                    e.file_path,
                    COALESCE(t.title, e.file_path) AS track_title,
                    e.start_seconds,
                    e.end_seconds,
                    e.text,
                    array_cosine_similarity(e.embedding, ?::FLOAT[768]) AS similarity
                FROM media_embeddings e
                LEFT JOIN track_metadata t ON (e.file_path = t.download_path OR e.track_id = t.id)
                WHERE e.playlist_title = ?
                ORDER BY similarity DESC
                LIMIT 5
                """,
                [query_vec, playlist_name],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    e.file_path,
                    COALESCE(t.title, e.file_path) AS track_title,
                    e.start_seconds,
                    e.end_seconds,
                    e.text,
                    array_cosine_similarity(e.embedding, ?::FLOAT[768]) AS similarity
                FROM media_embeddings e
                LEFT JOIN track_metadata t ON (e.file_path = t.download_path OR e.track_id = t.id)
                ORDER BY similarity DESC
                LIMIT 5
                """,
                [query_vec],
            ).fetchall()

    sources = []
    excerpts_text = []
    for r in rows:
        formatted_t = format_seconds(r[2])
        sources.append({
            "file_path": r[0],
            "track_title": r[1],
            "start_seconds": float(r[2]),
            "end_seconds": float(r[3]),
            "formatted_time": formatted_t,
            "text": r[4],
            "similarity": round(float(r[5]), 4),
        })
        excerpts_text.append(
            f"[Episode: {r[1]} | Timestamp: {formatted_t} ({r[2]:.1f}s - {r[3]:.1f}s)]\n\"{r[4]}\""
        )

    context_str = "\n\n".join(excerpts_text) if excerpts_text else "No transcript excerpts found in database."

    system_prompt = (
        f"You are an intelligent study assistant for the audio/video playlist '{playlist_name}'.\n"
        "Answer the student's question clearly, helpfully, and concisely based on the transcript excerpts provided below.\n"
        "Important Guidelines:\n"
        "1. Whenever you refer to an idea or quote from an episode, cite the timestamp using the format [MM:SS] or [Track Name @ MM:SS] so the student can jump directly to that video moment.\n"
        "2. If the excerpts do not contain the answer, say so honestly and provide any related insight from the material.\n\n"
        f"Retrieved Context Excerpts from '{playlist_name}':\n"
        f"{context_str}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    if isinstance(history, list):
        for h in history[-6:]:
            if isinstance(h, dict) and "role" in h and "content" in h:
                messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": message})

    try:
        res = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.3},
            },
            timeout=90,
        )
        res.raise_for_status()
        reply_content = res.json().get("message", {}).get("content", "")
    except Exception as e:
        return jsonify({"error": f"Failed to get response from Ollama model '{OLLAMA_MODEL}': {e}"}), 502

    return jsonify({
        "reply": reply_content,
        "sources": sources,
        "model": OLLAMA_MODEL,
        "playlist": playlist_name,
    })


@app.route("/api/playlists", methods=["GET"])
def api_playlists():
    """Returns dynamic playlist feed tree for updating client UI."""
    playlist_files, feeds_dict = get_playlists_feeds()
    FEEDS.update(feeds_dict)
    return jsonify({
        "playlists": playlist_files,
        "count": sum(len(g.get("feed_items", [])) for g in playlist_files),
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    """Queues a new background download task for YouTube, SoundCloud, or media URLs."""
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    auto_transcribe = bool(data.get("auto_transcribe", False))

    if not url:
        return jsonify({"error": "Media URL is required"}), 400

    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({"error": "Please provide a valid http/https URL"}), 400

    try:
        task_id = download_manager.start_download(url, auto_transcribe=auto_transcribe)
        return jsonify({
            "status": "success",
            "task_id": task_id,
            "message": "Download task queued successfully",
        }), 202
    except Exception as e:
        app.logger.exception(f"Failed to start download: {e}")
        return jsonify({"error": f"Failed to start download: {e}"}), 500


@app.route("/api/download/status", methods=["GET"])
def api_download_status():
    """Returns all recent download tasks and active worker count."""
    tasks = download_manager.get_all_tasks()
    active_count = download_manager.get_active_count()
    return jsonify({
        "tasks": tasks,
        "active_count": active_count,
    })


@app.route("/api/download/status/<task_id>", methods=["GET"])
def api_download_task_status(task_id):
    """Returns status of a specific background download task."""
    task = download_manager.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"task": task})


@app.route("/api/download/cancel/<task_id>", methods=["POST"])
def api_download_cancel(task_id):
    """Signals a running download task to abort."""
    cancelled = download_manager.cancel_task(task_id)
    if not cancelled:
        return jsonify({"error": "Task could not be cancelled or has already completed"}), 400
    return jsonify({"status": "cancelled", "task_id": task_id})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
