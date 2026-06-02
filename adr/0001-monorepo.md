# ADR 0001: Monorepo with uv workspaces

- **Status:** accepted
- **Date:** 2026-09-02

## Context

TraceFork ships as several packages with a shared release cadence: a
framework-independent core, a CLI, and one adapter package per integration
(OpenAI, httpx, Python tools; later LangGraph, OpenAI Agents, MCP, OTel).
The PRD (§25) recommends this layout. We need to decide where the packages
live and how they share tooling.

## Decision

Use a single repository with a [uv](https://docs.astral.sh/uv/) workspace.
Every shippable package lives under `packages/*` with its own `pyproject.toml`
and `src/` layout. The workspace root is a *virtual* project: it carries no
build system and holds only the shared developer toolchain (pytest,
pytest-asyncio, hypothesis, ruff, mypy) and workspace sources, so that
`uv sync` installs every member package editable into one environment.

The core package depends only on Pydantic. Adapter packages may add
integration dependencies, but nothing in `tracefork-core` may depend on an
agent framework (see [ADR 0004](0004-boundary-abstraction.md)).

## Consequences

- Cross-package changes are atomic: one commit, one PR, one CI run.
- One lockfile pins the whole development environment.
- Packages are published individually from the same tree.
- The `tracefork` import name (provided by `tracefork-core`) is distinct from
  the workspace-root project name; the root is never installed.
- Tooling configuration lives once at the root (ruff, mypy, pytest); package
  pyproject files stay minimal.
