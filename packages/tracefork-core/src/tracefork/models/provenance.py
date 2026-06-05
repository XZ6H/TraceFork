"""Provenance model and capture (TF-014).

Git information is captured best-effort: recording must never fail because git
is unavailable. All fields are nullable.
"""

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_GIT_TIMEOUT_SECONDS = 10


class Provenance(BaseModel):
    """Environment and change provenance for a recorded execution."""

    model_config = ConfigDict(extra="forbid")

    git_commit: str | None = None
    git_dirty: bool | None = None
    git_branch: str | None = None
    python_version: str | None = None
    tracefork_version: str | None = None


def capture_git_provenance(repo_dir: Path | None = None) -> Provenance:
    """Capture git provenance for *repo_dir*, degrading to nulls on any failure."""
    try:
        commit = _git_output(repo_dir, "rev-parse", "HEAD")
        status = _git_output(repo_dir, "status", "--porcelain")
        branch = _git_output(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return Provenance()
    return Provenance(
        git_commit=commit,
        git_dirty=status != "",
        git_branch=None if branch == "HEAD" else branch,
    )


def _git_output(cwd: Path | None, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return result.stdout.strip()
