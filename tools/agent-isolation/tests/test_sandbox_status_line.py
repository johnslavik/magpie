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

"""Tests for sandbox-status-line.sh.

The script renders one status-line row from a Claude Code statusLine
payload on stdin.  Tests drive it with a synthetic payload and a
throwaway ``HOME`` so the developer's own user-scope settings cannot
decide the result.

Two behaviours carry the weight here:

* the sandbox tag is read from the scope Claude Code itself resolves —
  the **main worktree**, for a session in a linked worktree as much as
  for one in the main checkout, and for a session in a subdirectory;
* a linked worktree renders as ``<repo>/<worktree>``, with the repo
  prefix that worktree directories conventionally carry stripped off
  the second half.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "sandbox-status-line.sh"

# Use the absolute path so tests that restrict PATH still launch bash correctly.
BASH = shutil.which("bash") or "/bin/bash"

ANSI = re.compile(r"\033\[[0-9;]*m")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run(cwd: Path, home: Path, model: str = "Opus 5") -> str:
    """Render the status line for ``cwd``; return it with colour stripped."""
    payload = json.dumps({"model": {"display_name": model}, "workspace": {"current_dir": str(cwd)}})
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            # Keep the PR-segment cache out of the developer's real one.
            "XDG_CACHE_HOME": str(home / ".cache"),
        }
    )
    result = subprocess.run(
        [BASH, str(SCRIPT)],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return ANSI.sub("", result.stdout)


def _home(tmp_path: Path, sandbox: dict | None = None) -> Path:
    """A throwaway HOME, optionally with a user-scope sandbox stanza."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    if sandbox is not None:
        _write_settings(home / ".claude" / "settings.json", sandbox)
    return home


def _write_settings(path: Path, sandbox: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sandbox": sandbox}))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@e.st", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    """An initialised repo with one commit, ready for `git worktree add`."""
    repo = tmp_path / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "README").write_text("x\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "init")
    return repo


def _worktree(repo: Path, path: Path, branch: str) -> Path:
    _git(repo, "worktree", "add", "-q", str(path), "-b", branch)
    return path


def _folder(line: str) -> str:
    """The folder segment — everything between the tag and the first pipe."""
    after_tag = line.split("]", 1)[1]
    return after_tag.split("|", 1)[0].strip()


# ---------------------------------------------------------------------------
# sandbox tag — precedence
# ---------------------------------------------------------------------------


class TestSandboxTag:
    def test_no_settings_anywhere_is_no_sandbox(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        assert _run(plain, _home(tmp_path)).startswith("[NO SANDBOX]")

    def test_project_settings_enable_the_tag(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write_settings(repo / ".claude" / "settings.json", {"enabled": True})
        assert _run(repo, _home(tmp_path)).startswith("[sandbox]")

    def test_auto_allow_gets_its_own_tag(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write_settings(
            repo / ".claude" / "settings.json",
            {"enabled": True, "autoAllowBashIfSandboxed": True},
        )
        assert _run(repo, _home(tmp_path)).startswith("[sandbox-auto]")

    def test_local_settings_win_over_shared(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write_settings(repo / ".claude" / "settings.json", {"enabled": True})
        _write_settings(repo / ".claude" / "settings.local.json", {"enabled": False})
        assert _run(repo, _home(tmp_path)).startswith("[NO SANDBOX]")

    def test_user_scope_is_the_fallback(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _run(repo, _home(tmp_path, {"enabled": True})).startswith("[sandbox]")

    def test_subdirectory_still_reads_project_scope(self, tmp_path: Path) -> None:
        """A session below the repo root resolves the same project scope."""
        repo = _repo(tmp_path)
        _write_settings(repo / ".claude" / "settings.local.json", {"enabled": False})
        sub = repo / "src" / "deep"
        sub.mkdir(parents=True)
        assert _run(sub, _home(tmp_path, {"enabled": True})).startswith("[NO SANDBOX]")


# ---------------------------------------------------------------------------
# sandbox tag — linked worktrees
# ---------------------------------------------------------------------------


class TestSandboxTagInWorktree:
    def test_reads_the_main_worktree_settings(self, tmp_path: Path) -> None:
        """The reported bug.

        `/sandbox` toggled inside a worktree session persists to the main
        checkout's settings.local.json, because that is the project scope
        Claude Code resolves. Reading the worktree's own `.claude/` found
        nothing, fell through to user scope, and painted a green
        `[sandbox]` on a session that had no sandbox at all.
        """
        repo = _repo(tmp_path)
        _write_settings(repo / ".claude" / "settings.local.json", {"enabled": False})
        wt = _worktree(repo, tmp_path / "repo.feature", "feature")

        assert _run(wt, _home(tmp_path, {"enabled": True})).startswith("[NO SANDBOX]")

    def test_worktree_filesystem_stanza_does_not_shadow_the_tag(self, tmp_path: Path) -> None:
        """The per-worktree file carries an allowlist and no `enabled` key."""
        repo = _repo(tmp_path)
        _write_settings(repo / ".claude" / "settings.local.json", {"enabled": True})
        wt = _worktree(repo, tmp_path / "repo.feature", "feature")
        _write_settings(
            wt / ".claude" / "settings.local.json",
            {"filesystem": {"allowRead": [str(wt)], "allowWrite": [str(wt)]}},
        )

        assert _run(wt, _home(tmp_path)).startswith("[sandbox]")

    def test_subdirectory_of_a_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write_settings(repo / ".claude" / "settings.local.json", {"enabled": False})
        wt = _worktree(repo, tmp_path / "repo.feature", "feature")
        sub = wt / "src"
        sub.mkdir()

        assert _run(sub, _home(tmp_path, {"enabled": True})).startswith("[NO SANDBOX]")


# ---------------------------------------------------------------------------
# folder segment
# ---------------------------------------------------------------------------


class TestFolderSegment:
    def test_plain_directory_shows_its_basename(self, tmp_path: Path) -> None:
        plain = tmp_path / "somewhere"
        plain.mkdir()
        assert _folder(_run(plain, _home(tmp_path))) == "somewhere"

    def test_repo_shows_the_repo_name(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _folder(_run(repo, _home(tmp_path))) == "repo"

    def test_subdirectory_shows_the_repo_name(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        sub = repo / "tools"
        sub.mkdir()
        assert _folder(_run(sub, _home(tmp_path))) == "repo"

    def test_dot_named_worktree_renders_as_repo_slash_slug(self, tmp_path: Path) -> None:
        """`<repo>.<slug>` — the layout worktree managers create."""
        repo = _repo(tmp_path)
        wt = _worktree(repo, tmp_path / "repo.gh-1234", "gh-1234")
        assert _folder(_run(wt, _home(tmp_path))) == "repo/gh-1234"

    def test_dash_named_worktree_renders_as_repo_slash_slug(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        wt = _worktree(repo, tmp_path / "repo-gh-1234", "gh-1234")
        assert _folder(_run(wt, _home(tmp_path))) == "repo/gh-1234"

    def test_unprefixed_worktree_keeps_its_whole_name(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        wt = _worktree(repo, tmp_path / "scratch", "scratch")
        assert _folder(_run(wt, _home(tmp_path))) == "repo/scratch"

    def test_claude_code_worktree_layout(self, tmp_path: Path) -> None:
        """`<source>/.claude/worktrees/<name>` is a linked worktree too."""
        repo = _repo(tmp_path)
        wt = _worktree(repo, repo / ".claude" / "worktrees" / "spike", "spike")
        assert _folder(_run(wt, _home(tmp_path))) == "repo/spike"

    def test_worktree_branch_is_the_worktree_s_own(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        wt = _worktree(repo, tmp_path / "repo.gh-1234", "gh-1234")
        assert "| gh-1234" in _run(wt, _home(tmp_path))

    def test_submodule_is_not_mistaken_for_a_worktree(self, tmp_path: Path) -> None:
        """A submodule's common git dir is `<super>/.git/modules/<name>`.

        Its parent is no project root, so the working tree we are
        standing in stays the answer — otherwise the folder segment
        would read `modules/sub` and the settings walk would look for
        `<super>/.git/modules/.claude/`.
        """
        super_repo = _repo(tmp_path, "super")
        lib = _repo(tmp_path / "libs", "lib")
        _git(super_repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(lib), "sub")

        assert _folder(_run(super_repo / "sub", _home(tmp_path))) == "sub"
