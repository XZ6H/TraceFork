"""TF-014: git provenance capture with graceful degradation."""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from tracefork.models import capture_git_provenance

_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_IDENTITY_ENV},
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "file.txt").write_text("content", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_captures_clean_repo(git_repo: Path) -> None:
    provenance = capture_git_provenance(git_repo)
    assert provenance.git_commit is not None
    assert len(provenance.git_commit) == 40
    assert provenance.git_dirty is False
    assert provenance.git_branch == "main"


def test_detects_dirty_worktree(git_repo: Path) -> None:
    (git_repo / "file.txt").write_text("changed", encoding="utf-8")
    provenance = capture_git_provenance(git_repo)
    assert provenance.git_dirty is True


def test_non_repo_directory_degrades_to_nulls(tmp_path: Path) -> None:
    provenance = capture_git_provenance(tmp_path)
    assert provenance.git_commit is None
    assert provenance.git_dirty is None
    assert provenance.git_branch is None


def test_detached_head_degrades_branch_to_null(git_repo: Path) -> None:
    _git(git_repo, "checkout", "--detach", "HEAD")
    provenance = capture_git_provenance(git_repo)
    assert provenance.git_commit is not None  # commit still known
    assert provenance.git_branch is None  # no branch on detached HEAD
    assert provenance.git_dirty is False
