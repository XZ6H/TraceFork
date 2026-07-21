# Test Plan & Coverage Matrix

How TraceFork tests itself. The suite is organized by *risk*, not by module:
every nondeterminism boundary, every fail-closed promise, and every public
failure path has dedicated cases. Run everything with `uv run pytest`.

## Test categories

| Category | Directory | Purpose |
|---|---|---|
| Unit | `tests/unit/` | Deterministic logic: models, matching, diff, assertions, CLI |
| Concurrency | `tests/concurrency/` | Async task trees, parallel boundaries, cancellation |
| Replay invariants | `tests/replay/` | The five product guarantees (plan §34) |
| Integration | `tests/integration/` | Real servers, real processes, end-to-end slices |
| Property | `tests/property/` | Hypothesis: round-trips, order independence, redaction |
| Performance | `tests/performance/` | PRD §37 targets with CI-safe margins |
| Live smoke | `scripts/live_smoke.py` | Gated real-API validation (maintainer task) |

## Coverage matrix (surface → cases → where)

### 1. Domain models
| Case | Status |
|---|---|
| Defaults, enums, required fields, empty IDs rejected | `test_models_trace.py` |
| UTC coercion (naive, +02:00 offsets) | `test_models_trace.py` |
| JSON round trip (span, error span, trace) | `test_models_trace.py` |
| Unknown extra fields rejected | `test_models_trace.py` |
| `completed_at >= started_at` enforced | `test_models_trace.py` |
| `occurrence >= 0` enforced | `test_models_trace.py` |
| Provenance: clean/dirty/non-repo/detached HEAD | `test_provenance.py` |

### 2. Canonicalization & fingerprints
| Case | Status |
|---|---|
| Sorted keys, compact separators, UTF-8 | `test_fixture_envelope.py` |
| Datetime → UTC `Z`; naive treated as UTC | `test_fixture_envelope.py` |
| Unsupported types & non-finite floats rejected | `test_fixture_envelope.py`, `test_fingerprint.py` |
| Insertion-order independence (property) | `test_property_*.py` |
| Ignore rules: top-level, dotted, any-level, list traversal | `test_canonicalization_rules.py` |
| Dict key collision (`1` vs `"1"`) rejected | `test_canonicalization_rules.py` |
| Fingerprint: stability, exclusions, ignore-rule scope | `test_fingerprint.py` |

### 3. Fixtures & storage
| Case | Status |
|---|---|
| Envelope round trip, digest stability, payload coverage | `test_fixture_envelope.py` |
| Corruption: tampered trace/digest, bad JSON, missing blocks | `test_fixture_envelope.py` |
| Version checks: fixture_version **and** trace `schema_version` | `test_fixture_envelope.py` |
| Graph validation at load: cycles, duplicates, missing parents | `test_serialization_graph.py` |
| Store: round trip, sorted list, traversal rejection, tamper | `test_filesystem_store.py` |
| Overwrite semantics | `test_filesystem_store.py` |

### 4. Recording engine
| Case | Status |
|---|---|
| record()/span() sync + async, defaults, input/metadata | `test_recording_context.py` |
| Body exception → FAILED trace; handled span error → COMPLETED | `test_recording_context.py`, `test_recording_spans.py` |
| Nested spans chain parents; sibling order = start order | `test_recording_spans.py`, `test_parallel_spans.py` |
| Parallel gather: common parent, completion-order independence | `test_parallel_spans.py` |
| Cancelled task records `CancelledError` span | `test_recording_edge.py` |
| Task isolation: nested record() inside a task | `test_recording_edge.py` |
| Leaked (never-exited) span swept to ERROR on finish | `test_recording_edge.py` |
| `finish()` idempotent | `test_recording_edge.py` |
| No stack traces persisted | `test_recording_spans.py` |

### 5. Boundary runtime
| Case | Status |
|---|---|
| NORMAL pass-through (no recording), RECORD captures | `test_boundary_runtime.py` |
| REPLAY without session fails closed | `test_boundary_runtime.py` |
| Unknown boundary type fails before any recording | `test_boundary_runtime.py` |
| Occurrence indices scoped by type/name/fingerprint/parent | `test_boundary_runtime.py` |
| LLM/tool family → span kind | `test_boundary_runtime.py` |
| Boundary error → error span + failed invocation metadata | `test_boundary_runtime.py` |
| Non-canonicalizable **request** fails before side effects | `test_boundary_runtime.py` |
| Non-canonicalizable **response** fails before side effects | `test_boundary_runtime.py` |
| Handler metadata merged under request metadata | `test_boundary_runtime.py` |
| Native (restored) response returned in RECORD mode too | `test_boundary_runtime.py` |

### 6. Matching & diagnostics
| Case | Status |
|---|---|
| Exact fingerprint match; repeated calls consume in order | `test_replay_matcher.py` |
| Parent-context preference; consumption exhaustion | `test_replay_matcher.py` |
| Miss: closest candidate + similarity; empty candidate set | `test_replay_matcher.py` |
| Difference lines (+/~/-); full mismatch message | `test_replay_matcher.py` |
| Recorded invocations without fingerprints never match | `test_replay_matcher.py` |

### 7. Replay engine
| Case | Status |
|---|---|
| Hermetic: zero live, zero network, all matched | `test_replay_session.py`, `test_replay_invariants.py` |
| Candidate trace: replayed spans/invocations marked | `test_replay_session.py` |
| Mismatch fails closed with diagnostics; status failed | `test_replay_session.py` |
| Unused recordings reported (warning, not failure) | `test_replay_session.py` |
| Selective: live LLM + replayed tools; exact tool override | `test_replay_session.py` |
| Live boundary that *raises* still counted as live | `test_replay_session.py` |
| `result` before `__enter__` rejected | `test_replay_session.py` |
| Parallel identical replays match distinct occurrences | `test_replay_concurrency.py` |
| Fixture never mutated; determinism; 1:1 correspondence | `test_replay_invariants.py` |
| Policy resolution: exact → family → default | `test_replay_policy.py` |

### 8. Adapters
| Case | Status |
|---|---|
| Python tools: fqn naming, args/kwargs capture, sync+async | `test_python_tools.py` |
| Argument canonicalization: dataclasses, Pydantic, enums, datetimes, UUIDs, deterministic sets | `test_python_tools.py` |
| Unsupported argument → AdapterError, no side effects | `test_python_tools.py` |
| Replay without executing the body; mismatch fails closed | `test_python_tools.py` |
| Custom boundary types (scripted LLM → llm family) | `test_python_tools.py` |
| OpenAI: record/replay, sync/async, streaming, usage, key hygiene, fail-closed | `test_openai_adapter.py` + live smoke |
| httpx: capture/redact/replay, hermetic server lifecycle, binary bodies | `test_httpx_adapter.py`, `test_httpx_hermetic.py` |

### 9. Metrics, graph, diff
| Case | Status |
|---|---|
| Call counts, token sums, null-when-unknown, latencies, cost | `test_metrics.py` |
| Graph: edges, duplicates, missing parents, cycles | `test_trajectory.py` |
| Logical trajectory defaults + kind filters | `test_trajectory.py` |
| Alignment: match/insert/remove, kind changes, first divergence | `test_diff.py` |
| Empty vs non-empty trajectories; filtered-to-empty | `test_diff.py` |
| Resource deltas with percentages; null baselines | `test_diff.py` |
| 1,000-node diff under PRD target | `test_performance.py` |

### 10. Assertions & suites
| Case | Status |
|---|---|
| require/forbid/before (interleaved, reversed, missing) | `test_assertions.py` |
| max_calls per tool; resource maximums; null-data evidence | `test_assertions.py` |
| YAML suite: pass, forbidden-tool fail, max-calls fail | `test_eval_suites.py` |
| Exit precedence: invalid(3) > error(2) > failed(1) | `test_eval_suites.py` |
| Per-case timeout; JSON + JUnit output; invalid YAML | `test_eval_suites.py` |
| JUnit XML escaping | `test_eval_suites.py` |

### 11. CLI
| Case | Status |
|---|---|
| init idempotent; inspect summary + invalid fixture (3) | `test_cli.py` |
| record: success, script args, failing script (2 + failed fixture), SystemExit propagation, missing script (3) | `test_cli.py` |
| replay: hermetic PASS, mismatch (1), `--live` invalid (3), bad entrypoint (3) | `test_cli.py` |
| diff report | `test_cli.py` |

### 12. Redaction & security
| Case | Status |
|---|---|
| Default secret keys, case-insensitive, any depth | `test_redaction.py` |
| Custom redactors after key redaction; extra keys; disable | `test_redaction.py` |
| Secrets never in fixture bytes (unit + live smoke) | `test_redaction.py`, `scripts/live_smoke.py` |
| Span inputs redacted before persistence | `test_redaction.py` |
| Fail-closed guarantees | `test_replay_invariants.py` |

### 13. Performance & packaging
| Case | Status |
|---|---|
| 100-step hermetic replay; 1,000-node diff; fixture load | `test_performance.py` |
| Wheels build; `py.typed` shipped | verified via `uv build` + wheel inspection |

## Known, deliberate exclusions

- **Multi-threaded recording** is supported for occurrence counting but not
  exhaustively tested; TraceFork is async-first by design.
- **Fixture signing** (cryptographic authentication) is a planned feature.
- **Real-provider matrix** (all model providers) is out of scope; the live
  smoke covers one OpenAI-compatible service, and the adapter contract keeps
  others behind the same tests.
