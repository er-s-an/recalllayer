# RecallLayer

**Local-first layered agent memory for [Hermes Agent](https://hermes-agent.nousresearch.com)** — a native **Memory Provider** plugin (`plugin.yaml` + `register(ctx)`), not a skill or prompt wrapper.

Remake: Mem0 / Zep / Letta-style persistent memory × Hermes Memory Provider rails.

Author: **Caesar / Jiachen Xie** · GitHub: [er-s-an](https://github.com/er-s-an)

## Why this exists

People who run Hermes across sessions and channels lose context on every reset. Skills and prompts cannot own exclusive `memory.provider` selection, `prefetch` / `sync_turn` lifecycle hooks, or profile-scoped durable storage. RecallLayer does — as a first-class Memory Provider.

## Layers

| Layer | Purpose | Written by |
|-------|---------|------------|
| **working** | Short-lived scratch (auto-expires ~6h) | Tools / demo |
| **episodic** | Turn and event memory | `sync_turn` + tools |
| **semantic** | Durable facts | Tools + builtin memory mirror |

Search and prefetch recompute after every store / update / delete (**retrieve-after-write**).

## Install (Hermes)

```bash
# Drop into the active profile's plugins directory
mkdir -p ~/.hermes/plugins
cp -R recalllayer ~/.hermes/plugins/recalllayer

# Select as the exclusive external memory provider
hermes config set memory.provider recalllayer

# Optional checks (same suite catalog CI uses)
hermes plugins doctor ~/.hermes/plugins/recalllayer --ci
hermes plugins validate ~/.hermes/plugins/recalllayer
hermes memory status
```

Optional settings in `$HERMES_HOME/recalllayer.json`:

```json
{
  "auto_recall": true,
  "auto_capture": true,
  "max_prefetch": 6
}
```

Data lives at `$HERMES_HOME/recalllayer/memories.db` (profile-scoped).

## Agent tools

- `recalllayer_remember` — store a fact (default layer: semantic)
- `recalllayer_search` — FTS / keyword recall across layers
- `recalllayer_update` — correct or promote a memory by id
- `recalllayer_forget` — delete by id
- `recalllayer_layers` — stats + optional per-layer list

## Live sandbox demo (no Hermes required)

```bash
cd /path/to/recalllayer-repo   # repo root that contains recalllayer/ and demo/
python demo/server.py --port 8765
# open http://127.0.0.1:8765/
```

Flow to show judges: **Fill sample facts → Search “timezone” → Edit a fact → Search again → Forget → Search empty**.

## Standalone store tests

```bash
python -m unittest tests.test_store -v
```

## Hermes Memory Provider mapping

| Hermes API | RecallLayer |
|------------|-------------|
| `register(ctx)` → `ctx.register_memory_provider(...)` | `RecallLayerProvider` (new instance per load) |
| `is_available` / `initialize` | Always available; SQLite under `hermes_home` |
| `system_prompt_block` | Static tool guidance |
| `prefetch` / `queue_prefetch` | Layer-ranked FTS recall |
| `sync_turn` | Non-blocking episodic write |
| `get_tool_schemas` / `handle_tool_call` | Five tools above |
| `on_memory_write` | Mirrors builtin MEMORY.md writes → semantic |
| `on_session_end` / `shutdown` | Flush sync thread; close DB |
| `cli.py` → `register_cli` | `hermes recalllayer status\|search\|…` when active |

## Disclosure (catalog rule 13)

- **Network:** none by default (local SQLite only).
- **Reads/writes:** `$HERMES_HOME/recalllayer/` and optional `$HERMES_HOME/recalllayer.json`.
- **Shell / background:** daemon threads for `sync_turn` and `queue_prefetch` only.
- **Credentials:** none required; no `.env` / `auth.json` reads.
- **Telemetry:** none.

## License

MIT
