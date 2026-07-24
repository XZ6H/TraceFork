---
name: live-smoke
description: Run TraceFork's gated live smoke suite against a real OpenAI-compatible API (118 checks). Use when the user asks to validate against a real LLM/provider or run live tests. Keys come from environment variables only and must NEVER be committed.
---

# Live smoke suite

`scripts/live_smoke.py` validates the full pipeline against real service
traffic: recording, hermetic replay, fail-closed mismatches, selective live
replay, tool-call loops, streaming, error capture, sync/async httpx, MOCK.

## Run

```console
LIVE_SMOKE_API_KEY=sk-or-v1-... \
LIVE_SMOKE_MODEL=meta/muse-spark-1.3-contributor \
LIVE_SMOKE_BASE_URL=https://openrouter.ai/api/v1 \
    uv run python scripts/live_smoke.py
```

- Optional: `LIVE_SMOKE_ONLY=record,streaming` to filter sections.
- Exit codes: 0 all checks passed, 1 failures, 3 key unset (skip).
- Cost: roughly 25 small API calls with capped tokens (fractions of a cent).
- Runtime: a few minutes.

## Rules

1. The key comes from environment variables only. **Never** write it into
   files, fixtures, logs, or commits. Before committing anything, verify:
   `git grep <key>` must return nothing.
2. Assertions target *mechanics* (record fidelity, matching, hermeticity,
   metadata) — never model creativity. A check that depends on what the
   model says will flake; rewrite it mechanically or make it a conditional
   skip.
3. Provider/model limitations (e.g. `tool_choice` support) become
   conditional `[SKIP]` sections, not failures.
4. Failures: fix the *check* if the check is wrong, the *product* if the
   product is wrong. Say which and why in the summary.
5. Sections can be selected with `LIVE_SMOKE_ONLY=`; sections needing a
   recorded fixture require the `record` section too.
