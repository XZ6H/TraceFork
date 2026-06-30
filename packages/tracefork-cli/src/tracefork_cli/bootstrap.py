"""Subprocess entry point for `tracefork record python SCRIPT`.

Runs the target script inside a recording session and persists the resulting
fixture under .tracefork/fixtures/. Target scripts obtain their instrumentation
from :mod:`tracefork.bootstrap`.
"""

import os
import runpy
import sys
from pathlib import Path

from tracefork import record
from tracefork.serialization import build_envelope
from tracefork.storage import FilesystemFixtureStore


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m tracefork_cli.bootstrap SCRIPT [ARGS...]", file=sys.stderr)
        return 3
    script, script_args = sys.argv[1], sys.argv[2:]
    sys.argv = [script, *script_args]
    name = os.environ.get("TRACEFORK_RECORD_NAME") or Path(script).stem

    error: BaseException | None = None
    with record(name) as rec:
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                error = exc
        except BaseException as exc:  # the failure is recorded in the trace
            error = exc

    fixture_dir = Path(".tracefork/fixtures")
    store = FilesystemFixtureStore(fixture_dir)
    store.save(name, build_envelope(rec.trace))
    print(f"fixture saved: {fixture_dir / (name + '.json')}")

    if error is None:
        return 0
    if isinstance(error, SystemExit):
        return int(error.code or 1)
    print(f"script failed: {type(error).__name__}: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
