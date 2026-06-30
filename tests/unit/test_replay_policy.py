"""TF-090 groundwork: replay policy resolution.

Resolution order: exact boundary rule, then boundary-type rule, then default.
"""

from tracefork.models import ReplayMode, ReplayPolicy


def test_default_mode_is_replay() -> None:
    policy = ReplayPolicy()
    assert policy.mode_for("tool.anything", "search") is ReplayMode.REPLAY
    assert policy.mode_for("llm.openai", "planner") is ReplayMode.REPLAY
    assert policy.mode_for("http", "GET") is ReplayMode.REPLAY


def test_type_rule_overrides_default() -> None:
    policy = ReplayPolicy(llm=ReplayMode.LIVE)
    assert policy.mode_for("llm.openai", "planner") is ReplayMode.LIVE
    assert policy.mode_for("llm", "planner") is ReplayMode.LIVE
    assert policy.mode_for("http", "GET") is ReplayMode.REPLAY


def test_http_type_rule() -> None:
    policy = ReplayPolicy(http=ReplayMode.LIVE)
    assert policy.mode_for("http", "GET /orders") is ReplayMode.LIVE
    assert policy.mode_for("llm.openai", "planner") is ReplayMode.REPLAY


def test_exact_tool_rule_overrides_everything() -> None:
    policy = ReplayPolicy(tools={"search_orders": ReplayMode.LIVE})
    assert policy.mode_for("tool.echo", "search_orders") is ReplayMode.LIVE
    assert policy.mode_for("tool.echo", "refund_order") is ReplayMode.REPLAY


def test_family_rule_overrides_default_but_not_exact() -> None:
    policy = ReplayPolicy(families={"tool": ReplayMode.LIVE})
    assert policy.mode_for("tool.python", "search") is ReplayMode.LIVE
    assert policy.mode_for("llm.openai", "planner") is ReplayMode.REPLAY
    exact = ReplayPolicy(families={"tool": ReplayMode.LIVE}, tools={"search": ReplayMode.REPLAY})
    assert exact.mode_for("tool.python", "search") is ReplayMode.REPLAY
    assert exact.mode_for("tool.python", "other") is ReplayMode.LIVE
