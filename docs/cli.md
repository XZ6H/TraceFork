# CLI Reference

The `tracefork` command (package `tracefork-cli`). All commands share one
exit-code contract, so CI only needs the number:

| Exit code | Meaning |
|---|---|
| 0 | success |
| 1 | regression / replay mismatch |
| 2 | execution error (agent code crashed) |
| 3 | invalid input, fixture, or usage |

Usage errors from the argument parser itself also exit 2 (click convention).

## Global

```console
tracefork --version    # print and exit
tracefork --help
```

## tracefork init

Creates the local project layout, idempotent:

```text
.tracefork/
  fixtures/   recorded fixtures live here (gitignore or commit deliberately)
  runs/       scratch space for run artifacts
  config.yaml reserved — nothing reads it yet (see the file's comments)
```

## tracefork record

```console
tracefork record [--name NAME] python SCRIPT [SCRIPT_ARGS...]
```

Runs `SCRIPT` in a subprocess with recording enabled and saves the resulting
fixture to `.tracefork/fixtures/<name>.json` (name defaults to the script
stem). The leading `python` keyword is accepted and ignored.

- Target scripts obtain instrumentation from the process bootstrap:
  `from tracefork.bootstrap import tools` gives a `ToolBox` bound to the
  recording runtime. SDK clients (e.g. `instrument_openai(client, runtime())`)
  are instrumented by the script itself.
- The script's exit code is propagated: `SystemExit(n)` -> `n`, an unhandled
  exception -> 2, success -> 0. A failed run still records its fixture with
  `trace.status = failed` and per-boundary error metadata.
- Script arguments after the script path are passed through unchanged
  (`sys.argv` is set to `[script, *args]`).

## tracefork inspect

```console
tracefork inspect FIXTURE.json
```

Prints a human-readable summary: name, status, span counts by kind, boundary
count, total tokens (when usage metadata exists), wall-clock duration.
Exit 3 on an invalid or missing fixture.

## tracefork replay

```console
tracefork replay FIXTURE.json \
    --entrypoint myapp.agent:run \
    [--live llm|http|tools]... \
    [--live-tool TOOL_NAME]...
```

Replays a fixture while running the *current* agent code. `--entrypoint`
(required) is `module:function` — an async callable receiving the fixture's
trace input; the module resolves relative to the current working directory.

- Policy resolution per boundary: exact tool rule (`--live-tool`) → family
  rule (`--live llm|http|tools`) → default (`replay`).
- Output reports replayed/live/unexpected/unused counts, the resolved policy,
  and PASS/FAIL.
- Exit 0 passed, 1 mismatch/failure (diagnostics on stderr), 2 execution
  error, 3 invalid fixture or input.

## tracefork diff

```console
tracefork diff BASELINE.json CANDIDATE.json
```

Prints the trajectory alignment (`+` inserted, `-` removed, matches), the
first divergence position, and resource deltas (tokens, cost, call counts,
duration) with change percentages. Informational: always exits 0 unless a
fixture is invalid (exit 3). Pass/fail verdicts belong to `eval` suites.

## tracefork eval

```console
tracefork eval SUITE.yaml [--format text|json|junit]
```

Runs an evaluation suite: YAML schema with `name`, `cases[]`, each case
having `fixture` (path relative to the suite file), `entrypoint`
(`module:function`, async callable receiving the trace input), optional
`replay` (policy: `default`, `llm`, `http`, `tools`, `mocks`) and optional
`expect` (assertions: `tools.require/forbid`, `before`, `max_calls`, `max`).
Cases run sequentially. Machine-readable output: `--format json` (structured
results incl. per-case detail and exit code) and `--format junit` (XML
`<testsuite>`, suitable for CI test reporting).

Exit-code precedence across cases: 3 invalid fixture/suite > 2 execution
error > 1 failed assertions > 0 all passed.

## Example suite

```yaml
name: support-agent-regressions
cases:
  - name: expired-order-rejected
    fixture: fixtures/fixed-run.json
    entrypoint: support_agent.agent:run
    replay:
      default: replay
      llm: live
      tools:
        search_orders: live
      mocks:
        weather: {"temperature": 21}
    expect:
      tools:
        require: [check_refund_policy]
        forbid: [refund_order]
      before:
        - [check_refund_policy, refund_order]
      max_calls:
        search_orders: 3
      max:
        tool_calls: 4
        llm_calls: 2
        total_tokens: 10000
        cost_usd: 0.05
```

See [testing.md](testing.md) for how all of this is tested, and
[../examples/support-agent](../examples/support-agent) for a full walkthrough.
