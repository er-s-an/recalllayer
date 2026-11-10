#!/usr/bin/env python3
"""RecallLayer sandbox demo — local HTTP API + static UI.

  python demo/server.py [--port 8765] [--db /tmp/recalllayer-demo.db]

Endpoints:
  GET  /           -> index.html
  GET  /api/stats
  GET  /api/list?layer=
  GET  /api/search?q=
  POST /api/remember  JSON {content, layer?, importance?, tags?}
  POST /api/update    JSON {memory_id, content?, layer?, importance?}
  POST /api/forget    JSON {memory_id}
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recalllayer.store import RecallStore  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
STORE: RecallStore | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, code: int, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        assert STORE is not None
        if path in ("/", "/index.html"):
            data = (DEMO_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/stats":
            return self._json(200, {"ok": True, **STORE.stats()})
        if path == "/api/list":
            layer = (qs.get("layer") or [None])[0]
            limit = int((qs.get("limit") or ["40"])[0])
            items = STORE.list(layer=layer or None, limit=limit)
            return self._json(200, {"ok": True, "items": [i.to_dict() for i in items]})
        if path == "/api/search":
            q = (qs.get("q") or [""])[0]
            layer = (qs.get("layer") or [None])[0]
            hits = STORE.search(q, layer=layer or None, top_k=12)
            return self._json(
                200,
                {
                    "ok": True,
                    "query": q,
                    "results": [{**i.to_dict(), "score": round(s, 4)} for i, s in hits],
                },
            )
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        assert STORE is not None
        try:
            body = self._read_json()
        except Exception as exc:
            return self._json(400, {"ok": False, "error": f"bad json: {exc}"})
        try:
            if parsed.path == "/api/remember":
                item = STORE.add(
                    body.get("content", ""),
                    layer=(body.get("layer") or "semantic"),
                    importance=float(body.get("importance") or 0.7),
                    tags=body.get("tags") or [],
                    source="demo",
                )
                return self._json(200, {"ok": True, "memory": item.to_dict()})
            if parsed.path == "/api/update":
                mid = body.get("memory_id") or ""
                kwargs = {}
                for k in ("content", "layer", "importance", "tags"):
                    if k in body and body[k] is not None:
                        kwargs[k] = body[k]
                item = STORE.update(mid, **kwargs)
                return self._json(200, {"ok": True, "memory": item.to_dict()})
            if parsed.path == "/api/forget":
                mid = body.get("memory_id") or ""
                return self._json(200, {"ok": STORE.delete(mid), "memory_id": mid})
        except KeyError as exc:
            return self._json(404, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        self._json(404, {"ok": False, "error": "not found"})


def main():
    global STORE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=str(Path("/tmp") / "recalllayer-demo.db"))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    STORE = RecallStore(Path(args.db))
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"RecallLayer demo → http://{args.host}:{args.port}/")
    print(f"DB: {args.db}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        STORE.close()


if __name__ == "__main__":
    main()
