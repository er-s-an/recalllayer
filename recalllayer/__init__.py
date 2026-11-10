"""RecallLayer — native Hermes Memory Provider.

Local-first layered agent memory (working / episodic / semantic) with SQLite + FTS5.
Implements the MemoryProvider ABC and registers via ctx.register_memory_provider().
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .store import LAYERS, RecallStore

logger = logging.getLogger(__name__)

# Import Hermes ABC when available; keep a lightweight stub so standalone demos
# and unit tests still import this package without a full Hermes install.
try:
    from agent.memory_provider import MemoryProvider, spawn_context_thread
    try:
        from agent.memory_provider import RecallStatus, is_trivial_prompt
    except ImportError:  # older Hermes
        RecallStatus = None  # type: ignore

        def is_trivial_prompt(text: Optional[str]) -> bool:  # type: ignore
            s = (text or "").strip()
            return (not s) or s.startswith("/")
except ImportError:  # pragma: no cover — standalone / demo path
    from abc import ABC, abstractmethod
    import threading

    class MemoryProvider(ABC):  # type: ignore
        pre_compress_checkpoint_api_version = 1

        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs) -> None: ...

        @abstractmethod
        def get_tool_schemas(self) -> list: ...

    def spawn_context_thread(target, *, name: str, daemon: bool = True, args=(), kwargs=None):
        return threading.Thread(
            target=target, args=args, kwargs=kwargs or {}, name=name, daemon=daemon
        )

    RecallStatus = None  # type: ignore

    def is_trivial_prompt(text: Optional[str]) -> bool:
        s = (text or "").strip()
        return (not s) or s.startswith("/")


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


TOOL_SCHEMAS = [
    _schema(
        "recalllayer_remember",
        (
            "Store a durable fact in RecallLayer. Use for lasting preferences, decisions, "
            "people, projects, or corrections. Prefer layer='semantic' for facts that must "
            "survive across sessions; use 'working' for short-lived scratch; 'episodic' for "
            "event notes. Returns the memory id."
        ),
        {
            "content": {"type": "string", "description": "The fact or note to store."},
            "layer": {
                "type": "string",
                "enum": list(LAYERS),
                "description": "Memory layer (default: semantic).",
            },
            "importance": {
                "type": "number",
                "description": "0.0–1.0 importance (default 0.7 for semantic).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional short tags.",
            },
        },
        ["content"],
    ),
    _schema(
        "recalllayer_search",
        (
            "Search RecallLayer memories by meaning/keywords across layers. Call before "
            "answering questions that may depend on prior user facts, preferences, or events. "
            "Results recompute after every remember/update/forget (retrieve-after-write)."
        ),
        {
            "query": {"type": "string", "description": "What to search for."},
            "layer": {
                "type": "string",
                "enum": list(LAYERS),
                "description": "Optional layer filter.",
            },
            "top_k": {
                "type": "integer",
                "description": "Max results (default 8, max 50).",
            },
        },
        ["query"],
    ),
    _schema(
        "recalllayer_update",
        (
            "Update an existing memory by id (from recalllayer_search). Use when a stored "
            "fact changed or was wrong — correct in place instead of duplicating."
        ),
        {
            "memory_id": {"type": "string", "description": "Memory id to update."},
            "content": {"type": "string", "description": "New text content."},
            "layer": {
                "type": "string",
                "enum": list(LAYERS),
                "description": "Optional new layer (e.g. promote working→semantic).",
            },
            "importance": {"type": "number", "description": "Optional new importance 0–1."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional replacement tags.",
            },
        },
        ["memory_id"],
    ),
    _schema(
        "recalllayer_forget",
        (
            "Delete a memory by id (from recalllayer_search). Use when a fact is obsolete "
            "or the user asks to forget it. Prefer recalllayer_update if the fact merely changed."
        ),
        {
            "memory_id": {"type": "string", "description": "Memory id to delete."},
        },
        ["memory_id"],
    ),
    _schema(
        "recalllayer_layers",
        (
            "Inspect RecallLayer store stats and optionally list recent items per layer. "
            "Use to see what is stored in working / episodic / semantic memory."
        ),
        {
            "layer": {
                "type": "string",
                "enum": list(LAYERS),
                "description": "Optional layer to list recent items from.",
            },
            "limit": {
                "type": "integer",
                "description": "Max items to list when layer is set (default 10).",
            },
        },
        [],
    ),
]


class RecallLayerProvider(MemoryProvider):
    """Local-first layered Memory Provider for Hermes."""

    def __init__(self) -> None:
        self._store: Optional[RecallStore] = None
        self._session_id = ""
        self._hermes_home = ""
        self._agent_context = "primary"
        self._prefetch_cache = ""
        self._last_recall_count = 0
        self._sync_thread = None
        self._auto_capture = True
        self._auto_recall = True
        self._max_prefetch = 6

    @property
    def name(self) -> str:
        return "recalllayer"

    def is_available(self) -> bool:
        # Local SQLite — always available; no network / API key required.
        return True

    def unavailable_reason(self) -> str:
        return ""

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "auto_recall",
                "description": "Prefetch relevant memories before each turn",
                "default": True,
            },
            {
                "key": "auto_capture",
                "description": "Write completed turns into episodic memory",
                "default": True,
            },
            {
                "key": "max_prefetch",
                "description": "Max memories injected per turn",
                "default": 6,
            },
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        path = Path(hermes_home) / "recalllayer.json"
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update({k: v for k, v in values.items() if v is not None})
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home") or str(Path.home() / ".hermes")
        self._hermes_home = str(hermes_home)
        self._session_id = session_id or ""
        self._agent_context = kwargs.get("agent_context") or "primary"

        cfg_path = Path(hermes_home) / "recalllayer.json"
        cfg: dict[str, Any] = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("RecallLayer: bad config %s: %s", cfg_path, exc)

        self._auto_capture = bool(cfg.get("auto_capture", True))
        self._auto_recall = bool(cfg.get("auto_recall", True))
        try:
            self._max_prefetch = max(1, min(20, int(cfg.get("max_prefetch", 6))))
        except (TypeError, ValueError):
            self._max_prefetch = 6

        db_path = Path(hermes_home) / "recalllayer" / "memories.db"
        self._store = RecallStore(db_path)
        self._store.purge_expired()
        logger.info(
            "RecallLayer initialized session=%s db=%s context=%s",
            self._session_id,
            db_path,
            self._agent_context,
        )

    def system_prompt_block(self) -> str:
        return (
            "## RecallLayer memory\n"
            "You have a local layered memory provider (working / episodic / semantic).\n"
            "- Use recalllayer_search before answering questions that may depend on prior facts.\n"
            "- Use recalllayer_remember for lasting preferences, decisions, and personal details.\n"
            "- Use recalllayer_update / recalllayer_forget to correct or remove stored facts.\n"
            "- Prefer semantic for durable facts; working for short-lived scratch; episodic is "
            "also filled automatically from completed turns.\n"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._auto_recall or self._store is None:
            return ""
        if is_trivial_prompt(query):
            self._last_recall_count = 0
            return ""
        sid = session_id or self._session_id
        # Prefer cache warmed by queue_prefetch when query matches loosely
        if self._prefetch_cache and not query:
            text = self._prefetch_cache
            self._prefetch_cache = ""
            return text

        hits = self._store.search(query or "", top_k=self._max_prefetch)
        # Prefer semantic, then episodic, then working
        layer_rank = {"semantic": 0, "episodic": 1, "working": 2}
        hits.sort(key=lambda pair: (layer_rank.get(pair[0].layer, 9), -pair[1], -pair[0].importance))
        hits = hits[: self._max_prefetch]
        self._last_recall_count = len(hits)
        if not hits:
            return ""
        lines = ["Recalled from RecallLayer (prior sessions / layers):"]
        for item, score in hits:
            tag = f"[{item.layer} #{item.id} ~{score:.2f}]"
            lines.append(f"- {tag} {item.content}")
        return "\n".join(lines)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._auto_recall or self._store is None:
            return
        if is_trivial_prompt(query):
            return

        def _warm() -> None:
            try:
                self._prefetch_cache = self.prefetch(query, session_id=session_id or self._session_id)
            except Exception as exc:
                logger.warning("RecallLayer queue_prefetch failed: %s", exc)

        t = spawn_context_thread(_warm, name="recalllayer-prefetch")
        t.start()

    def recall_status(self):
        if RecallStatus is None or self._last_recall_count <= 0:
            return None
        return RecallStatus(
            provider_label="RecallLayer",
            count=self._last_recall_count,
            glyph="🧠",
        )

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[list] = None,
        turn_author: Optional[dict] = None,
    ) -> None:
        if not self._auto_capture or self._store is None:
            return
        if self._agent_context != "primary":
            return
        user_content = (user_content or "").strip()
        assistant_content = (assistant_content or "").strip()
        if not user_content and not assistant_content:
            return
        if is_trivial_prompt(user_content):
            return
        sid = session_id or self._session_id

        def _sync() -> None:
            try:
                snippet = f"User: {user_content[:500]}"
                if assistant_content:
                    snippet += f"\nAssistant: {assistant_content[:500]}"
                assert self._store is not None
                self._store.add(
                    snippet,
                    layer="episodic",
                    importance=0.4,
                    tags=["turn"],
                    session_id=sid,
                    source="sync_turn",
                    metadata={"turn_author": turn_author or {}},
                )
            except Exception as exc:
                logger.warning("RecallLayer sync_turn failed: %s", exc)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = spawn_context_thread(_sync, name="recalllayer-sync")
        self._sync_thread.start()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        if self._store is None:
            return json.dumps({"ok": False, "error": "RecallLayer not initialized"})
        try:
            if tool_name in ("recalllayer_remember", "recalllayer-remember"):
                return self._tool_remember(args)
            if tool_name in ("recalllayer_search", "recalllayer-search"):
                return self._tool_search(args)
            if tool_name in ("recalllayer_update", "recalllayer-update"):
                return self._tool_update(args)
            if tool_name in ("recalllayer_forget", "recalllayer-forget"):
                return self._tool_forget(args)
            if tool_name in ("recalllayer_layers", "recalllayer-layers"):
                return self._tool_layers(args)
            return json.dumps({"ok": False, "error": f"unknown tool: {tool_name}"})
        except Exception as exc:
            logger.exception("RecallLayer tool error")
            return json.dumps({"ok": False, "error": str(exc)})

    def on_session_end(self, messages: list) -> None:
        if self._store is None or self._agent_context != "primary":
            return
        # Soft flush: ensure pending sync finished
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

    def on_memory_write(self, action: str, target: str, content: str, metadata=None) -> None:
        """Mirror built-in MEMORY.md writes into semantic layer."""
        if self._store is None or not content:
            return
        if self._agent_context != "primary":
            return
        try:
            text = f"[builtin:{action}:{target}] {content}".strip()
            self._store.add(
                text[:2000],
                layer="semantic",
                importance=0.6,
                tags=["builtin", str(target or "memory")],
                session_id=self._session_id,
                source="on_memory_write",
                metadata={"action": action, "target": target, **(metadata or {})},
            )
        except Exception as exc:
            logger.warning("RecallLayer on_memory_write failed: %s", exc)

    def shutdown(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        if self._store is not None:
            self._store.close()
            self._store = None

    # -- tools -----------------------------------------------------------------

    def _tool_remember(self, args: dict) -> str:
        content = (args.get("content") or "").strip()
        if not content:
            return json.dumps({"ok": False, "error": "content required"})
        layer = (args.get("layer") or "semantic").strip().lower()
        if layer not in LAYERS:
            layer = "semantic"
        importance = args.get("importance")
        if importance is None:
            importance = 0.7 if layer == "semantic" else 0.5
        tags = args.get("tags") or []
        item = self._store.add(  # type: ignore[union-attr]
            content,
            layer=layer,
            importance=float(importance),
            tags=tags if isinstance(tags, list) else [str(tags)],
            session_id=self._session_id,
            source="tool",
        )
        return json.dumps({"ok": True, "memory": item.to_dict()}, indent=2)

    def _tool_search(self, args: dict) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return json.dumps({"ok": False, "error": "query required"})
        layer = args.get("layer")
        if layer:
            layer = str(layer).strip().lower()
            if layer not in LAYERS:
                layer = None
        top_k = int(args.get("top_k") or 8)
        hits = self._store.search(query, layer=layer, top_k=top_k)  # type: ignore[union-attr]
        return json.dumps(
            {
                "ok": True,
                "query": query,
                "count": len(hits),
                "results": [
                    {**item.to_dict(), "score": round(score, 4)} for item, score in hits
                ],
            },
            indent=2,
        )

    def _tool_update(self, args: dict) -> str:
        memory_id = (args.get("memory_id") or "").strip()
        if not memory_id:
            return json.dumps({"ok": False, "error": "memory_id required"})
        kwargs: dict[str, Any] = {}
        if args.get("content") is not None:
            kwargs["content"] = args["content"]
        if args.get("layer") is not None:
            kwargs["layer"] = args["layer"]
        if args.get("importance") is not None:
            kwargs["importance"] = args["importance"]
        if args.get("tags") is not None:
            kwargs["tags"] = args["tags"]
        if not kwargs:
            return json.dumps({"ok": False, "error": "nothing to update"})
        try:
            item = self._store.update(memory_id, **kwargs)  # type: ignore[union-attr]
        except KeyError:
            return json.dumps({"ok": False, "error": f"memory not found: {memory_id}"})
        return json.dumps({"ok": True, "memory": item.to_dict()}, indent=2)

    def _tool_forget(self, args: dict) -> str:
        memory_id = (args.get("memory_id") or "").strip()
        if not memory_id:
            return json.dumps({"ok": False, "error": "memory_id required"})
        deleted = self._store.delete(memory_id)  # type: ignore[union-attr]
        return json.dumps({"ok": deleted, "memory_id": memory_id})

    def _tool_layers(self, args: dict) -> str:
        stats = self._store.stats()  # type: ignore[union-attr]
        payload: dict[str, Any] = {"ok": True, "stats": stats}
        layer = args.get("layer")
        if layer:
            layer = str(layer).strip().lower()
            if layer in LAYERS:
                limit = int(args.get("limit") or 10)
                items = self._store.list(layer=layer, limit=limit)  # type: ignore[union-attr]
                payload["items"] = [i.to_dict() for i in items]
        return json.dumps(payload, indent=2)


def register(ctx) -> None:
    """Hermes memory plugin entry point — new instance per load (profile isolation)."""
    ctx.register_memory_provider(RecallLayerProvider())
