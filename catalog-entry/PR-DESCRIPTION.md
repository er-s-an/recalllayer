# Catalog entry: recalllayer (DRAFT — not submitted)

## What it does
RecallLayer is a native Hermes **Memory Provider** (not a skill). It gives agents local-first layered memory — working / episodic / semantic — with SQLite + FTS5, retrieve-after-write tools, automatic turn sync into episodic memory, and prefetch into each turn.

## Hermes surfaces
- `ctx.register_memory_provider(RecallLayerProvider())`
- Lifecycle: `initialize`, `prefetch`, `queue_prefetch`, `sync_turn`, `on_memory_write`, `on_session_end`, `shutdown`
- Tools: `recalllayer_remember|search|update|forget|layers`
- Optional CLI via `cli.py` → `register_cli`

## Disclosure (rule 13)
- No network calls by default
- Writes only under `$HERMES_HOME/recalllayer/` (+ optional `recalllayer.json`)
- No credentials; no telemetry
- Background daemon threads for sync/prefetch only

## Validation
```bash
hermes plugins validate ./recalllayer
hermes plugins doctor ./recalllayer --ci
```

## Screenshots
See demo sandbox (`python demo/server.py`) — write → search → update → forget.

**Status:** Local draft only. Official PR waits for user「交」.
