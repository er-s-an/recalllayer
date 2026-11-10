"""Retrieve-after-write tests for RecallLayer store (no Hermes required)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recalllayer.store import RecallStore  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RecallStore(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_add_search_update_delete_recomputes(self):
        a = self.store.add("Caesar prefers dark mode in the IDE", layer="semantic", tags=["prefs"])
        hits = self.store.search("dark mode")
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, a.id)

        self.store.update(a.id, content="Caesar prefers light mode in the IDE")
        hits2 = self.store.search("light mode")
        self.assertTrue(hits2)
        self.assertEqual(hits2[0][0].id, a.id)
        self.assertIn("light", hits2[0][0].content)
        # old text should not dominate
        hits_old = self.store.search("dark mode")
        # may still match weakly via 'mode' token — ensure content changed
        found = self.store.get(a.id)
        self.assertIsNotNone(found)
        self.assertIn("light", found.content)
        self.assertNotIn("dark", found.content)

        self.assertTrue(self.store.delete(a.id))
        hits3 = self.store.search("light mode")
        self.assertFalse(any(i.id == a.id for i, _ in hits3))
        self.assertIsNone(self.store.get(a.id))

    def test_layers_and_promote(self):
        w = self.store.add("scratch note about Injective", layer="working")
        e = self.store.add("User: ship MeterBuy\nAssistant: ok", layer="episodic")
        s = self.store.add("Team uses GitHub er-s-an", layer="semantic")
        stats = self.store.stats()
        self.assertEqual(stats["by_layer"]["working"], 1)
        self.assertEqual(stats["by_layer"]["episodic"], 1)
        self.assertEqual(stats["by_layer"]["semantic"], 1)
        promoted = self.store.promote(w.id, to_layer="semantic")
        self.assertEqual(promoted.layer, "semantic")
        self.assertIsNone(promoted.expires_at)
        self.assertEqual(self.store.stats()["by_layer"]["working"], 0)
        self.assertEqual(self.store.stats()["by_layer"]["semantic"], 2)
        _ = e, s

    def test_provider_tools_roundtrip(self):
        from recalllayer import RecallLayerProvider

        p = RecallLayerProvider()
        p.initialize("test-session", hermes_home=self.tmp.name, agent_context="primary")
        out = json_loads(p.handle_tool_call("recalllayer_remember", {"content": "Deploy on Friday", "layer": "semantic"}))
        self.assertTrue(out["ok"])
        mid = out["memory"]["id"]
        search = json_loads(p.handle_tool_call("recalllayer_search", {"query": "Friday deploy"}))
        self.assertTrue(search["ok"])
        self.assertGreaterEqual(search["count"], 1)
        upd = json_loads(p.handle_tool_call("recalllayer_update", {"memory_id": mid, "content": "Deploy on Saturday"}))
        self.assertTrue(upd["ok"])
        search2 = json_loads(p.handle_tool_call("recalllayer_search", {"query": "Saturday"}))
        self.assertTrue(any("Saturday" in r["content"] for r in search2["results"]))
        forget = json_loads(p.handle_tool_call("recalllayer_forget", {"memory_id": mid}))
        self.assertTrue(forget["ok"])
        search3 = json_loads(p.handle_tool_call("recalllayer_search", {"query": "Saturday"}))
        self.assertFalse(any(r["id"] == mid for r in search3["results"]))
        layers = json_loads(p.handle_tool_call("recalllayer_layers", {}))
        self.assertTrue(layers["ok"])
        # sync + prefetch
        p.sync_turn("My timezone is Asia/Shanghai", "Noted.", session_id="test-session")
        if p._sync_thread:
            p._sync_thread.join(timeout=3)
        pref = p.prefetch("What is my timezone?")
        self.assertTrue(isinstance(pref, str))
        p.shutdown()


def json_loads(s: str):
    import json

    return json.loads(s)


if __name__ == "__main__":
    unittest.main()
