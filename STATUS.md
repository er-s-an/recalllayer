# Hermes Agent Plugin Hackathon — STATUS

Updated: 2026-11-10 ~09:33 HKT (Asia/Shanghai)

## State
BUILD READY — plugin + public repo + live demo + English onetake pitch published.
Product: RecallLayer (Mem0/Zep/Letta-style persistent memory as a native Hermes Memory Provider).
No official catalog PR opened.
No Typeform filled.
No X post.

## Skills loaded
hackathon-concept-forge; 创赛 idea 门 (workflows/idea); onetake.

## Deliverables
- Plugin: `/workspace/competitions/hermes-plugin/recalllayer/` (`plugin.yaml` + `register(ctx)` Memory Provider)
- Public repo: https://github.com/er-s-an/recalllayer
- Demo sandbox: `python demo/server.py --port 8765`
- Tests: `python -m unittest tests.test_store -v` PASS (retrieve-after-write)
- Hermes check (v0.19.0): `memory.provider=recalllayer` → Plugin installed ✓ / Status available ✓ / active
- Pitch video: https://review-clips.vercel.app/v/recalllayer-pitch (~36s English onetake)
- Catalog draft only: `catalog-entry/recalllayer.yaml` — wait user「交」before official PR / Typeform / X

## Idea (locked)
Who: Hermes users who lose context across sessions/channels.
What: Durable layered memory (working / episodic / semantic) with real store→update→delete→retrieve.
Why Memory Provider: exclusive `memory.provider`, prefetch/sync_turn lifecycle, profile-scoped SQLite — not doable as skill/prompt.

## Hard gates
- Zero entry fee; do not bind a card
- No Typeform / X / official catalog PR without「交」
