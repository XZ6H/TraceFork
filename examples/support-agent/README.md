# Support Agent Demo (incident-1821)

A deliberately realistic demo, built per the TraceFork plan: a customer
support agent refunds an expired order. The policy tool would have denied the
refund — prompt v10 never asked. This is the production incident.

The demo runs fully offline: the "LLM" is a scripted planner behind a
`llm.demo` boundary, the tools are backed by a seeded in-process SQLite
database. Everything you see below is reproducible with no API keys.

## Layout

```text
support_agent/
  db.py            seeded SQLite (order 31991 is expired)
  tools.py         get_customer, get_order, check_refund_policy, refund_order
  agent.py         prompt v11 (fixed): always check the policy first
  agent_buggy.py   prompt v10 (incident): refunds right after lookup
suite.yaml         regression suite over the fixed run
fixtures/          committed fixtures (regenerate with generate_fixtures.py)
```

## The walkthrough

Run everything from this directory:

```console
cd examples/support-agent
```

**1. Replay the incident hermetically** — the buggy agent still runs its
recorded plan, with zero live calls (the plumbing still works against the
recorded world):

```console
$ tracefork replay fixtures/incident-1821.json --entrypoint support_agent.agent_buggy:run
Replay: incident-1821
  LLM calls replayed:   1
  Tool calls replayed:  3
  Live calls:           0
  Unexpected calls:     0
  PASS
```

**2. Run the current (fixed) code against the incident** — live planner
(v11), replayed tools. The fixed agent calls `check_refund_policy`, which was
never recorded in the incident. Replay **fails closed** with the delta:

```console
$ tracefork replay fixtures/incident-1821.json --entrypoint support_agent.agent:run --live llm
replay mismatch
Replay mismatch: no recorded interaction matches tool.python.check_refund_policy
Received:
{"args":[31991],"kwargs":{}}
...
```

No live call escaped: the missing tool call surfaces as a mismatch instead.

**3. Diff the incident against the fixed run**:

```console
$ tracefork diff fixtures/incident-1821.json fixtures/fixed-run.json
TRACE REGRESSION REPORT
baseline:  incident-1821
candidate: fixed-run

Trajectory
  llm:planner
  tool:get_order
  tool:get_customer
- tool:refund_order
+ tool:check_refund_policy

First divergence: removed at alignment step 3
```

**4. Turn the fix into a CI regression suite**:

```console
$ tracefork eval suite.yaml
support-agent-regressions

1 case(s)

  PASS expired-order-rejected

1 passed
0 failed
```

The suite asserts the fixed agent still checks the policy (`require`) and
never refunds directly (`forbid`). Run it in CI — it is deterministic and
hermetic.

## Recording your own incident

```console
tracefork record --name incident-1821 python -c "import asyncio; from support_agent import agent_buggy; asyncio.run(agent_buggy.run({'order_id': 31991}))"
```

The committed fixtures come from `generate_fixtures.py` (maintainer task).
