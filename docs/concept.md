# RecallLayer — Concept Anchor

## Who benefits
People who run Hermes across sessions and messaging channels (CLI, Telegram, Discord, Desktop) and lose context every time a chat resets. They currently paste prior facts, keep ad-hoc notes, or hope MEMORY.md still has the right line.

## What changes
RecallLayer makes memory a first-class exclusive Memory Provider: durable store → update → delete → retrieve that recomputes after every change. The agent gets tools to remember and search; Hermes auto-prefetches relevant layers before each turn and syncs completed turns into episodic memory.

## Why a Memory Provider (not a skill or prompt)
- Skills and prompts cannot own exclusive `memory.provider` selection, lifecycle hooks (`prefetch`, `sync_turn`, `on_session_end`), or profile-scoped durable storage under `$HERMES_HOME`.
- Only a native `plugin.yaml` + `register(ctx)` Memory Provider can inject recalled context every turn without polluting the system-prompt cache, and expose tools Hermes routes through `handle_tool_call`.
- Judges prefer plugins a prompt or skill cannot do — this is exactly that surface.

## Mechanism (Mem0 / Zep / Letta remake × Hermes rails)
Three layers inspired by Letta-style agent memory, implemented locally with SQLite + FTS5 (no paid API required):

1. **Working** — short-lived session scratch (auto-expires).
2. **Episodic** — turn transcripts and events (auto-written by `sync_turn`).
3. **Semantic** — durable facts the agent promotes via tools (searchable, editable, deletable).

Prefetch ranks semantic hits first, then recent episodic context matching the query. Updates and deletes immediately change subsequent search and prefetch results (retrieve-after-write is the demoable proof).

## Differentiation
- vs Mem0/Honcho/Supermemory: local-first, zero API key, offline demoable.
- vs Holographic: explicit layered architecture + promotion path + polished live sandbox viewer (write → persist → recall).
- vs built-in MEMORY.md: exclusive provider lifecycle, FTS search tools, cross-session SQLite under the active profile.

## Demo proof
Sandbox UI / CLI: type an arbitrary fact → store → search → update → search again (new text) → delete → search empty. Same store Hermes uses when `memory.provider: recalllayer`.

## Open / later
- Optional embeddings backend (still local-first by default).
- Catalog PR + Typeform + X video wait for user「交」.
