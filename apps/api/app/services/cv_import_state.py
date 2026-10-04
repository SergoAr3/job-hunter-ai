"""Short-lived, shared-worker CV previews; never stores document bytes/text.

The directory must be shared by API workers on the same host. Multi-host
deployments must route an import to that host (or replace this temporary store).
Claim is durable before domain writes: a crash cannot replay an uncertain Apply.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

TTL_SECONDS = 15 * 60


class CVImportError(Exception):
    def __init__(self, code: str, status: int = 409, field_errors=None):
        self.code, self.status, self.field_errors = code, status, field_errors


class ImportStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _connect(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        connection = sqlite3.connect(self.directory / "previews.sqlite", timeout=5)
        os.chmod(self.directory / "previews.sqlite", 0o600)
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("""CREATE TABLE IF NOT EXISTS previews (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            session_hash TEXT NOT NULL, expires REAL NOT NULL,
            payload TEXT, state TEXT NOT NULL DEFAULT 'ready')""")
        return connection

    def cleanup(self):
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM previews WHERE expires <= ?", (time.time(),))

    def issue(self, user_id: int, session_hash: str, payload: dict):
        raw = secrets.token_urlsafe(32)
        expiry = time.time() + TTL_SECONDS
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM previews WHERE expires <= ?", (time.time(),))
            total = connection.execute("SELECT count(*) FROM previews").fetchone()[0]
            owned = connection.execute("SELECT count(*) FROM previews WHERE user_id = ?", (user_id,)).fetchone()[0]
            if total >= 500 or owned >= 5:
                raise CVImportError("cv_import_busy", 429)
            connection.execute("INSERT INTO previews(token_hash,user_id,session_hash,expires,payload) VALUES(?,?,?,?,?)",
                               (self._hash(raw), user_id, session_hash, expiry, json.dumps(payload)))
        return raw, expiry

    @staticmethod
    def _hash(raw: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
            raise CVImportError("cv_import_invalid", 404)
        return hashlib.sha256(raw.encode()).hexdigest()

    def claim(self, raw: str, user_id: int, session_hash: str, revision: int = 1):
        key = self._hash(raw)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload,state,expires FROM previews WHERE token_hash=? AND user_id=? AND session_hash=?",
                                     (key, user_id, session_hash)).fetchone()
            if row is None:
                raise CVImportError("cv_import_invalid", 404)
            if row[2] <= time.time():
                raise CVImportError("cv_import_expired", 410)
            if row[1] != "ready":
                raise CVImportError("cv_import_used")
            payload = json.loads(row[0])
            if payload.get("revision", 1) != revision:
                raise CVImportError("cv_import_revision_stale")
            # Erase preview immediately; keep only a terminal token tombstone.
            connection.execute("UPDATE previews SET state='used',payload=NULL WHERE token_hash=?", (key,))
            return payload

    def edit(self, raw: str, user_id: int, session_hash: str, revision: int, update):
        key = self._hash(raw)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload,state,expires FROM previews WHERE token_hash=? AND user_id=? AND session_hash=?",
                                     (key, user_id, session_hash)).fetchone()
            if row is None:
                raise CVImportError("cv_import_invalid", 404)
            if row[2] <= time.time():
                raise CVImportError("cv_import_expired", 410)
            if row[1] != "ready":
                raise CVImportError("cv_import_used")
            payload = json.loads(row[0])
            if payload.get("revision", 1) != revision:
                raise CVImportError("cv_import_revision_stale")
            updated = update(payload)
            updated["revision"] = revision + 1
            # TTL and quota row remain unchanged. Claim/edit serialize on this DB.
            connection.execute("UPDATE previews SET payload=? WHERE token_hash=?", (json.dumps(updated), key))
            return updated, row[2]

    def cancel(self, raw: str, user_id: int, session_hash: str):
        key = self._hash(raw)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("UPDATE previews SET state='cancelled',payload=NULL WHERE token_hash=? AND user_id=? AND session_hash=? AND state='ready'",
                                        (key, user_id, session_hash))
            if cursor.rowcount != 1:
                raise CVImportError("cv_import_invalid", 404)


def get_import_store():
    directory = os.getenv("CV_IMPORT_STATE_DIR")
    return ImportStore(Path(directory) if directory else Path(tempfile.gettempdir()) / f"job-hunter-cv-imports-{os.getuid()}")
