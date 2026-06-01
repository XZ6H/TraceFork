"""M0 smoke test: the core package is importable through the workspace."""

import tracefork


def test_core_package_imports() -> None:
    assert tracefork.__version__ == "0.1.0"
