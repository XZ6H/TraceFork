"""Agent tools, instrumented through the process-wide bootstrap runtime."""

from typing import Any

from tracefork.bootstrap import tools

from support_agent import db


@tools.tool(name="get_customer")
async def get_customer(customer_id: int) -> dict[str, Any]:
    row = db.connection.execute(
        "SELECT customer_id, name, email FROM customers WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    return {"customer_id": row["customer_id"], "name": row["name"], "email": row["email"]}


@tools.tool(name="get_order")
async def get_order(order_id: int) -> dict[str, Any]:
    row = db.connection.execute(
        "SELECT order_id, customer_id, status, expires_on, amount FROM orders WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    return {
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "status": row["status"],
        "expires_on": row["expires_on"],
        "amount": row["amount"],
    }


@tools.tool(name="check_refund_policy")
async def check_refund_policy(order_id: int) -> dict[str, Any]:
    row = db.connection.execute(
        "SELECT expires_on FROM orders WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    if row["expires_on"] < db.REFERENCE_DATE:
        return {"decision": "deny", "reason": "order expired before the refund window"}
    return {"decision": "approve"}


@tools.tool(name="refund_order")
async def refund_order(order_id: int) -> dict[str, Any]:
    db.connection.execute("UPDATE orders SET status = 'refunded' WHERE order_id = ?", (order_id,))
    return {"order_id": order_id, "status": "refunded"}
