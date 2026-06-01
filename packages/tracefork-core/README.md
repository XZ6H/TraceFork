# tracefork-core

Framework-independent core engine for TraceFork: trace domain models, the
recording engine, the boundary abstraction, canonicalization and matching, and
the hermetic replay engine.

This package must never depend on an agent framework. Framework support lives
in adapter packages (`tracefork-openai`, `tracefork-langgraph`, ...).
