"""Regenerate the committed demo fixtures (maintainer task).

Run from examples/support-agent/:

    uv run python generate_fixtures.py
"""

import asyncio
from pathlib import Path

from support_agent import agent, agent_buggy
from tracefork import record
from tracefork.serialization import build_envelope
from tracefork.storage import FilesystemFixtureStore

FIXTURES = Path(__file__).parent / "fixtures"
INPUT = {"order_id": 31991}


async def main() -> None:
    store = FilesystemFixtureStore(FIXTURES)

    with record("incident-1821", input=INPUT) as incident:
        await agent_buggy.run(INPUT)
    store.save("incident-1821", build_envelope(incident.trace))
    print(f"wrote {FIXTURES / 'incident-1821.json'}")

    with record("fixed-run", input=INPUT) as fixed:
        await agent.run(INPUT)
    store.save("fixed-run", build_envelope(fixed.trace))
    print(f"wrote {FIXTURES / 'fixed-run.json'}")


if __name__ == "__main__":
    asyncio.run(main())
