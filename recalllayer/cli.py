"""RecallLayer CLI — hermes recalllayer <status|search|remember|stats|demo-path>."""

from __future__ import annotations

import json
from pathlib import Path


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        import os

        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _store():
    from .store import RecallStore

    db = _home() / "recalllayer" / "memories.db"
    return RecallStore(db)


def recalllayer_command(args):
    sub = getattr(args, "recalllayer_command", None)
    if sub == "status":
        store = _store()
        print(json.dumps({"ok": True, "provider": "recalllayer", **store.stats()}, indent=2))
        store.close()
    elif sub == "stats":
        store = _store()
        print(json.dumps(store.stats(), indent=2))
        store.close()
    elif sub == "search":
        query = getattr(args, "query", "") or ""
        store = _store()
        hits = store.search(query, top_k=getattr(args, "top_k", 8) or 8)
        print(
            json.dumps(
                [
                    {"id": i.id, "layer": i.layer, "score": round(s, 4), "content": i.content}
                    for i, s in hits
                ],
                indent=2,
            )
        )
        store.close()
    elif sub == "remember":
        content = getattr(args, "content", "") or ""
        layer = getattr(args, "layer", "semantic") or "semantic"
        store = _store()
        item = store.add(content, layer=layer, importance=0.7, source="cli")
        print(json.dumps(item.to_dict(), indent=2))
        store.close()
    elif sub == "demo-path":
        root = Path(__file__).resolve().parent.parent / "demo"
        print(str(root / "index.html"))
    else:
        print("Usage: hermes recalllayer <status|stats|search|remember|demo-path>")


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="recalllayer_command")
    subs.add_parser("status", help="Show provider status and counts")
    subs.add_parser("stats", help="Show layer counts")
    p_search = subs.add_parser("search", help="Search memories")
    p_search.add_argument("query", nargs="?", default="")
    p_search.add_argument("--top-k", dest="top_k", type=int, default=8)
    p_rem = subs.add_parser("remember", help="Store a semantic fact")
    p_rem.add_argument("content")
    p_rem.add_argument("--layer", default="semantic")
    subs.add_parser("demo-path", help="Print path to the HTML sandbox demo")
    subparser.set_defaults(func=recalllayer_command)
