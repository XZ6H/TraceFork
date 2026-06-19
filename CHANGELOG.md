# Changelog

All notable changes to TraceFork are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Trace domain models (`Trace`, `Span`, `Provenance`) with strict validation,
  UTC timestamp normalization and JSON round-tripping (TF-010).
- Canonical JSON serialization and a versioned fixture envelope with SHA-256
  integrity verification (TF-011, TF-013).
- `FilesystemFixtureStore` with atomic writes and path-traversal-safe names
  (TF-012); git provenance capture with graceful degradation (TF-014).
- Recording engine: `record()` / `span()` context managers backed by
  `contextvars`, with nested spans, parallel `asyncio.gather` support,
  start-order sibling ordering and exception capture without stack traces
  (TF-020–024).
- Boundary abstraction: `BoundaryRuntime` routing NORMAL/RECORD/REPLAY modes,
  `BoundaryRegistry`, `BoundaryInvocation` recording with fingerprints and
  occurrence indices; REPLAY without a session fails closed (TF-030–033).
- Canonicalization: ignore rules (dotted paths + any-level bare names),
  stable SHA-256 fingerprints, occurrence-aware `ReplayMatcher` and
  `ReplayMismatchError` diagnostics with similarity and structural diff
  (TF-040–045).
- Hermetic replay: `ReplaySession` with per-boundary policy resolution
  (REPLAY/LIVE; MOCK/FAULT fail closed), candidate-trace generation,
  result reports (matched/unmatched/unused/live/network) and replay
  invariant tests (TF-050–054).
- Python tool adapter: `ToolBox`/`@tools.tool()` routing sync and async
  functions through the boundary runtime, argument canonicalization
  (dataclasses, Pydantic models, enums, datetimes, UUIDs, deterministic
  sets), and offline replay that never executes the real function body
  (TF-060–063). Vertical slice: record → store → replay → mismatch fails
  closed.
- Documentation: concepts, trace schema, matching semantics, replay
  semantics, failure model, adapter contract and architecture.
- Repository foundation: uv workspace, quality gates (pytest,
  pytest-asyncio, hypothesis, ruff, mypy strict), governance files,
  ADRs 0001–0005 and GitHub Actions CI.
