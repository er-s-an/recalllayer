"""RecallLayer durable store — SQLite + FTS5, layered memory.

Layers:
  working   — short-lived session scratch (auto-expires)
  episodic  — turn / event memories (sync_turn writes here)
  semantic  — durable facts (agent tools promote / edit / forget)

All mutations recompute search results immediately (retrieve-after-write).
stdlib only — no third-party deps.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

LAYERS = ("working", "episodic", "semantic")
DEFAULT_WORKING_TTL_SECS = 6 * 60 * 60  # 6 hours
_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


@dataclass
class MemoryItem:
    id: str
    layer: str
    content: str
    importance: float
    tags: list[str]
    session_id: str
    source: str
    created_at: float
    updated_at: float
    expires_at: Optional[float] = None
    metadata: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tags"] = list(self.tags or [])
        d["metadata"] = dict(self.metadata or {})
        return d


class RecallStore:
    """Thread-safe layered memory store backed by SQLite + FTS5."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    layer TEXT NOT NULL CHECK(layer IN ('working','episodic','semantic')),
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    tags TEXT NOT NULL DEFAULT '[]',
                    session_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'tool',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
                CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
                CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content,
                    tags,
                    layer UNINDEXED,
                    id UNINDEXED,
                    content='memories',
                    content_rowid='rowid'
                );
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content, tags, layer, id)
                    VALUES (new.rowid, new.content, new.tags, new.layer, new.id);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, tags, layer, id)
                    VALUES ('delete', old.rowid, old.content, old.tags, old.layer, old.id);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, tags, layer, id)
                    VALUES ('delete', old.rowid, old.content, old.tags, old.layer, old.id);
                    INSERT INTO memories_fts(rowid, content, tags, layer, id)
                    VALUES (new.rowid, new.content, new.tags, new.layer, new.id);
                END;
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def purge_expired(self, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            self._conn.commit()
            return cur.rowcount

    def add(
        self,
        content: str,
        *,
        layer: str = "semantic",
        importance: float = 0.5,
        tags: Optional[Iterable[str]] = None,
        session_id: str = "",
        source: str = "tool",
        expires_at: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
        memory_id: Optional[str] = None,
    ) -> MemoryItem:
        content = (content or "").strip()
        if not content:
            raise ValueError("content is required")
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}")
        importance = max(0.0, min(1.0, float(importance)))
        now = time.time()
        if layer == "working" and expires_at is None:
            expires_at = now + DEFAULT_WORKING_TTL_SECS
        item = MemoryItem(
            id=memory_id or uuid.uuid4().hex[:12],
            layer=layer,
            content=content,
            importance=importance,
            tags=[t.strip() for t in (tags or []) if str(t).strip()],
            session_id=session_id or "",
            source=source,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self.purge_expired(now)
            self._conn.execute(
                """
                INSERT INTO memories
                (id, layer, content, importance, tags, session_id, source,
                 created_at, updated_at, expires_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.layer,
                    item.content,
                    item.importance,
                    json.dumps(item.tags),
                    item.session_id,
                    item.source,
                    item.created_at,
                    item.updated_at,
                    item.expires_at,
                    json.dumps(item.metadata),
                ),
            )
            self._conn.commit()
        return item

    def get(self, memory_id: str) -> Optional[MemoryItem]:
        with self._lock:
            self.purge_expired()
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def update(
        self,
        memory_id: str,
        *,
        content: Optional[str] = None,
        layer: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[Iterable[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MemoryItem:
        item = self.get(memory_id)
        if item is None:
            raise KeyError(f"memory not found: {memory_id}")
        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError("content cannot be empty")
            item.content = content
        if layer is not None:
            if layer not in LAYERS:
                raise ValueError(f"layer must be one of {LAYERS}")
            item.layer = layer
        if importance is not None:
            item.importance = max(0.0, min(1.0, float(importance)))
        if tags is not None:
            item.tags = [t.strip() for t in tags if str(t).strip()]
        if metadata is not None:
            item.metadata = dict(metadata)
        item.updated_at = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE memories SET
                    layer=?, content=?, importance=?, tags=?,
                    updated_at=?, metadata=?
                WHERE id=?
                """,
                (
                    item.layer,
                    item.content,
                    item.importance,
                    json.dumps(item.tags),
                    item.updated_at,
                    json.dumps(item.metadata or {}),
                    item.id,
                ),
            )
            self._conn.commit()
        return item

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def list(
        self,
        *,
        layer: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        limit = max(1, min(200, int(limit)))
        clauses: list[str] = []
        params: list[Any] = []
        if layer:
            clauses.append("layer = ?")
            params.append(layer)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM memories {where} ORDER BY importance DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            self.purge_expired()
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    def search(
        self,
        query: str,
        *,
        layer: Optional[str] = None,
        top_k: int = 8,
        session_id: Optional[str] = None,
    ) -> list[tuple[MemoryItem, float]]:
        """FTS search with LIKE fallback. Returns (item, score) sorted best-first."""
        query = (query or "").strip()
        top_k = max(1, min(50, int(top_k)))
        with self._lock:
            self.purge_expired()
            results: list[tuple[MemoryItem, float]] = []
            if query:
                fts_query = self._to_fts_query(query)
                if fts_query:
                    sql = """
                        SELECT m.*, bm25(memories_fts) AS rank
                        FROM memories_fts
                        JOIN memories m ON m.id = memories_fts.id
                        WHERE memories_fts MATCH ?
                    """
                    params: list[Any] = [fts_query]
                    if layer:
                        sql += " AND m.layer = ?"
                        params.append(layer)
                    if session_id is not None:
                        sql += " AND m.session_id = ?"
                        params.append(session_id)
                    sql += " ORDER BY rank LIMIT ?"
                    params.append(top_k)
                    try:
                        rows = self._conn.execute(sql, params).fetchall()
                        for row in rows:
                            # bm25: lower is better; convert to 0..1-ish score
                            rank = float(row["rank"])
                            score = 1.0 / (1.0 + max(0.0, rank))
                            score = min(1.0, score + 0.15 * float(row["importance"]))
                            results.append((self._row_to_item(row), score))
                    except sqlite3.OperationalError:
                        results = []
                if not results:
                    # LIKE fallback for short / punctuation-heavy queries
                    like = f"%{query}%"
                    sql = "SELECT * FROM memories WHERE content LIKE ?"
                    params = [like]
                    if layer:
                        sql += " AND layer = ?"
                        params.append(layer)
                    if session_id is not None:
                        sql += " AND session_id = ?"
                        params.append(session_id)
                    sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
                    params.append(top_k)
                    rows = self._conn.execute(sql, params).fetchall()
                    for row in rows:
                        item = self._row_to_item(row)
                        score = 0.55 + 0.3 * item.importance
                        results.append((item, score))
            else:
                for item in self.list(layer=layer, session_id=session_id, limit=top_k):
                    results.append((item, item.importance))
            results.sort(key=lambda x: (-x[1], -x[0].updated_at))
            return results[:top_k]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self.purge_expired()
            rows = self._conn.execute(
                "SELECT layer, COUNT(*) AS n FROM memories GROUP BY layer"
            ).fetchall()
            total = self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        by_layer = {layer: 0 for layer in LAYERS}
        for row in rows:
            by_layer[row["layer"]] = int(row["n"])
        return {
            "total": int(total),
            "by_layer": by_layer,
            "db_path": str(self.db_path),
        }

    def promote(self, memory_id: str, *, to_layer: str = "semantic") -> MemoryItem:
        if to_layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}")
        item = self.update(memory_id, layer=to_layer)
        if to_layer != "working":
            with self._lock:
                self._conn.execute(
                    "UPDATE memories SET expires_at = NULL WHERE id = ?",
                    (memory_id,),
                )
                self._conn.commit()
            item.expires_at = None
        return item

    @staticmethod
    def _to_fts_query(query: str) -> str:
        terms = _WORD_RE.findall(query)
        if not terms:
            return ""
        # OR-match tokens; quote each for safety
        return " OR ".join(f'"{t}"' for t in terms[:12])

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            id=row["id"],
            layer=row["layer"],
            content=row["content"],
            importance=float(row["importance"]),
            tags=json.loads(row["tags"] or "[]"),
            session_id=row["session_id"] or "",
            source=row["source"] or "tool",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=row["expires_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )
