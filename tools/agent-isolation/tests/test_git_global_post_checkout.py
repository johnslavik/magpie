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

"""Tests for git-global-post-checkout.sh.

The hook fires after every checkout, clone, and `git worktree add` on
the host. Its one job is to call the sandbox-allowlist helper for
Claude-Code-aware repos — and to stay silent, and exit 0, everywhere
else. The helper itself is stubbed here with a recorder so these tests
cover the hook's gate, not the helper's writes (those live in
test_sandbox_add_project_root.py).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "git-global-post-checkout.sh"

# Use the absolute path so tests that restrict PATH still launch bash correctly.
BASH = shutil.which("bash") or "/bin/bash"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_home(tmp_path: Path) -> tuple[Path, Path]:
    """A HOME whose sandbox-allowlist helper only records that it ran."""
    home = tmp_path / "home"
    scripts = home / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / "invoked"
    helper = scripts / "sandbox-add-project-root.sh"
    helper.write_text(f'#!/bin/sh\npwd >> "{marker}"\n')
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return home, marker


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


def _run(cwd: Path, home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [BASH, str(SCRIPT)], cwd=str(cwd), env=env, capture_output=True, text=True
    )


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


class TestHookGate:
    def test_fires_in_a_claude_aware_checkout(self, tmp_path: Path) -> None:
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        (repo / ".claude").mkdir()

        result = _run(repo, home)

        assert result.returncode == 0
        assert marker.exists()

    def test_stays_silent_in_an_unaware_repo(self, tmp_path: Path) -> None:
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)

        result = _run(repo, home)

        assert result.returncode == 0
        assert not marker.exists()

    def test_fires_in_a_fresh_worktree_of_an_aware_repo(self, tmp_path: Path) -> None:
        """A just-created worktree has no `.claude/` of its own.

        `.claude/` is gitignored by convention, so `git worktree add`
        leaves the new tree without one — and the hook fires inside that
        new tree. Gating on the worktree's own directory meant the
        allowlist was never written for exactly the checkout that needed
        it most.
        """
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        (repo / ".claude").mkdir()
        wt = tmp_path / "repo.feature"
        _git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
        assert not (wt / ".claude").exists()

        result = _run(wt, home)

        assert result.returncode == 0
        assert marker.exists()

    def test_stays_silent_in_a_worktree_of_an_unaware_repo(self, tmp_path: Path) -> None:
        home, marker = _fake_home(tmp_path)
        repo = _repo(tmp_path)
        wt = tmp_path / "repo.feature"
        _git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")

        result = _run(wt, home)

        assert result.returncode == 0
        assert not marker.exists()

    def test_exits_zero_outside_a_git_repo(self, tmp_path: Path) -> None:
        home, marker = _fake_home(tmp_path)
        plain = tmp_path / "plain"
        plain.mkdir()

        result = _run(plain, home)

        assert result.returncode == 0
        assert not marker.exists()
