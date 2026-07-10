# Changelog

All notable changes to TraceFork are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Trace model and persistence**: `Trace`/`Span`/`Provenance` domain models
  with strict validation and UTC normalization (TF-010); canonical JSON
  serialization; versioned fixture envelopes with SHA-256 integrity
  verification (TF-011, TF-013); `FilesystemFixtureStore` with atomic writes
  and traversal-safe names (TF-012); git provenance capture with graceful
  degradation (TF-014).
- **Recording engine**: `record()`/`span()` context managers on `contextvars`,
  nested spans, parallel `asyncio.gather` support with start-order sibling
  ordering, exception capture without stack traces (TF-020–024).
- **Boundary abstraction**: `BoundaryRuntime` routing NORMAL/RECORD/REPLAY
  modes, `BoundaryRegistry`, invocation recording with fingerprints and
  occurrence indices; REPLAY without a session fails closed (TF-030–033).
- **Canonicalization and matching**: ignore rules (dotted paths + any-level
  bare names), stable SHA-256 fingerprints, occurrence-aware `ReplayMatcher`
  (fingerprint → parent context → occurrence) and `ReplayMismatchError`
  diagnostics with similarity and structural diff (TF-040–045).
- **Hermetic and selective replay**: `ReplaySession` with per-boundary policy
  resolution (exact tool → family → default), candidate-trace generation,
  result reports (matched/unmatched/unused/live/network) and five replay
  invariant test suites (TF-050–054, TF-090–094).
- **Adapters**: Python tools (`ToolBox`, custom boundary types, argument
  canonicalization for dataclasses/Pydantic/enums/datetimes/UUIDs/sets,
  TF-060–063); OpenAI Responses API (sync + async, usage/latency metadata,
  streaming event recording and replay, SDK-object restore, TF-070–074);
  httpx (boundary-routed transport, secret-safe header defaults, hermetic
  server-lifecycle test, TF-080–082).
- **CLI**: `init`, `record` (subprocess bootstrap), `inspect`, `replay`
  (policies + `--live`/`--live-tool`), `diff`, `eval` with CI exit codes
  0/1/2/3 (TF-100–104, TF-152).
- **Metrics and graph**: `TraceMetrics` extraction (calls, tokens, cost,
  latencies), `CostCalculator` protocol with `TableCostCalculator` (unknown
  models → null, not zero, TF-110–111); `ExecutionGraph` derivation and
  validation, logical trajectory extraction (TF-120–122).
- **Diff engine**: deterministic LCS trajectory alignment, resource deltas
  with change percentages, first-divergence detection, CLI regression report
  (TF-130–135).
- **Assertions and eval suites**: `require`/`forbid`/`before`/`max_calls`/
  resource maximums with evidence, YAML suite schema, sequential runner,
  exit-code precedence, text/JSON/JUnit output (TF-140–145, TF-150–153,
  TF-181–182).
- **Redaction and security**: key-based redaction on by default (secret keys,
  case-insensitive, any depth), custom `@tracefork.redactor` hooks, applied
  before persistence — secrets never reach disk; security tests assert secret
  values never appear in fixture bytes (TF-170–173).
- **Demo**: `examples/support-agent` — the production-incident walkthrough
  (record incident → hermetic replay → fixed-agent replay fails closed →
  trajectory diff → CI regression suite), fully offline with committed
  fixtures (TF-160–163).
- **Documentation**: concepts, architecture, trace schema, matching,
  replay semantics, failure model, adapter contract, trajectory diff,
  security, GitHub Actions example; ADRs 0001–0005.
- **Repository foundation**: uv workspace, quality gates (pytest,
  pytest-asyncio, hypothesis, ruff, mypy strict), governance files, GitHub
  Actions CI (TF-001–004).
