"""Support agent — prompt v10 (the production incident): refunds without a policy check.

This is the code that caused incident-1821: order 31991 was expired, the
policy tool would have denied the refund, but v10 never asked.
"""

from typing import Any

from tracefork.bootstrap import tools

from support_agent import tools as agent_tools

PROMPT_VERSION = "v10"


@tools.tool(boundary_type="llm.demo", name="planner")
async def planner(order_id: int) -> dict[str, Any]:
    """Prompt v10 (buggy): refund immediately after lookup."""
    return {"plan": ["get_order", "get_customer", "refund_order"]}


async def run(input_data: dict[str, Any]) -> dict[str, Any]:
    order_id = input_data["order_id"]
    plan = (await planner(order_id))["plan"]

    decision: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    for step in plan:
        if step == "get_order":
            order = await agent_tools.get_order(order_id)
        elif step == "get_customer":
            customer = await agent_tools.get_customer(order["customer_id"])
        elif step == "check_refund_policy":
            decision = await agent_tools.check_refund_policy(order_id)
            if decision["decision"] == "deny":
                break
        elif step == "refund_order":
            decision = await agent_tools.refund_order(order_id)

    return {"prompt_version": PROMPT_VERSION, "customer": customer, "decision": decision}
