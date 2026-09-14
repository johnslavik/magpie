# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Tests for git-hook-dispatcher.sh — the post-checkout framework logic.

The dispatcher is invoked through a symlink named after the hook, so
these tests call it as `post-checkout`. Covered here: the same
Claude-Code-aware gate the standalone `git-global-post-checkout.sh`
carries, including the fresh-worktree case, plus the passthrough to a
repo-local hook.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "git-hook-dispatcher.sh"

# Use the absolute path so tests that restrict PATH still launch bash correctly.
BASH = shutil.which("bash") or "/bin/bash"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_home(tmp_path: Path) -> tuple[Path, Path]:
    """A HOME whose sandbox-allowlist helper only records that it ran."""
    home = tmp_path / "home"
    marker = tmp_path / "invoked"
    _executable(
        home / ".claude" / "scripts" / "sandbox-add-project-root.sh",
        f'#!/bin/sh\npwd >> "{marker}"\n',
    )
    return home, marker


def _hook_link(tmp_path: Path) -> Path:
    """The dispatcher, reachable under its `post-checkout` name."""
    link = tmp_path / "hooks" / "post-checkout"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(SCRIPT)
    return link


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@e.st", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "README").write_text("x\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "init")
    return repo


def _run(cwd: Path, home: Path, hook: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [BASH, str(hook)], cwd=str(cwd), env=env, capture_output=True, text=True
    )


# ---------------------------------------------------------------------------
# the post-checkout gate
# ---------------------------------------------------------------------------


class TestPostCheckoutGate:
    def test_fires_in_a_claude_aware_checkout(self, tmp_path: Path) -> None:
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        (repo / ".claude").mkdir()

        result = _run(repo, home, _hook_link(tmp_path))

        assert result.returncode == 0
        assert marker.exists()

    def test_fires_in_a_fresh_worktree_of_an_aware_repo(self, tmp_path: Path) -> None:
        """`git worktree add` leaves the new tree without a `.claude/`."""
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        (repo / ".claude").mkdir()
        wt = tmp_path / "repo.feature"
        _git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")

        result = _run(wt, home, _hook_link(tmp_path))

        assert result.returncode == 0
        assert marker.exists()

    def test_stays_silent_in_an_unaware_repo(self, tmp_path: Path) -> None:
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)

        result = _run(repo, home, _hook_link(tmp_path))

        assert result.returncode == 0
        assert not marker.exists()


# ---------------------------------------------------------------------------
# passthrough to the repo-local hook
# ---------------------------------------------------------------------------


class TestLocalHookPassthrough:
    def test_chains_to_the_repo_local_hook(self, tmp_path: Path) -> None:
        home, _ = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        ran = tmp_path / "local-hook-ran"
        _executable(repo / ".git" / "hooks" / "post-checkout", f'#!/bin/sh\ntouch "{ran}"\n')

        result = _run(repo, home, _hook_link(tmp_path))

        assert result.returncode == 0
        assert ran.exists()

    def test_chains_from_a_worktree_to_the_main_checkout_hook(self, tmp_path: Path) -> None:
        home, _ = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        ran = tmp_path / "local-hook-ran"
        _executable(repo / ".git" / "hooks" / "post-checkout", f'#!/bin/sh\ntouch "{ran}"\n')
        wt = tmp_path / "repo.feature"
        _git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")

        result = _run(wt, home, _hook_link(tmp_path))

        assert result.returncode == 0
        assert ran.exists()
