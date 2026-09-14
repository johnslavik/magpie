#!/usr/bin/env bash
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
#
# git-global-post-checkout.sh — the universal post-checkout hook
# installed under ~/.claude/git-hooks/post-checkout when the
# operator picks **whole-user** scope in
# `setup-isolated-setup-install`.
#
# Activated by:
#   git config --global core.hooksPath ~/.claude/git-hooks/
#
# After that, every `git checkout`, `git clone` (which runs an
# implicit checkout of the default branch), and `git worktree add`
# across the operator's host invokes this script — for any repo,
# not just apache-magpie adopters.
#
# One responsibility, best-effort + idempotent + `|| true` so the
# hook never breaks the surrounding git operation:
#
#   **Sandbox-allowlist for the current worktree.** If the repo is
#   Claude-Code-aware (a `.claude/` directory in this working tree
#   *or* in the main checkout — a freshly-created worktree has none
#   of its own yet, the directory being gitignored) and the
#   framework's `sandbox-add-project-root.sh` helper is installed at
#   `~/.claude/scripts/`, the hook calls the helper to populate
#   `<worktree>/.claude/settings.local.json` with the worktree's
#   absolute path. From a linked worktree the helper also writes the
#   main checkout's `settings.local.json`, which is the file Claude
#   Code reads for a worktree session. Defensive against the harness
#   behaviour documented at
#   https://github.com/apache/magpie/issues/197 .
#
# **Not** in this hook: apache-magpie symlink reconciliation.
# `/magpie-setup verify --auto-fix-symlinks` is a Claude Code
# slash command, not a shell command, so it cannot be invoked from
# a `git checkout` hook running in the operator's shell — the
# previous template version of this hook spelled out the line
# anyway and printed a spurious "No such file or directory" error
# on every checkout. Symlink-drift in magpie-adopted repos is now
# reconciled **lazily** — the next time the operator opens Claude
# Code in the worktree, `/magpie-setup verify` detects any drift
# and offers to fix it.
#
# IMPORTANT — `core.hooksPath` shadowing. When `core.hooksPath` is
# set globally, git looks ONLY in that directory for hooks. Every
# per-repo `<repo>/.git/hooks/*` becomes inert across every repo
# on the host. If the operator has hooks they care about in
# specific repos (per-commit linters, pre-push checks, etc.) those
# need to be migrated into the global hooks dir or invoked from
# within these global hooks. The install skill warns the operator
# about this trade-off loudly before setting `core.hooksPath`.
#
# This hook only handles `post-checkout`. Other hook types
# (pre-commit, commit-msg, pre-push, ...) need their own files in
# the global hooks dir if the operator wants them to fire after
# setting `core.hooksPath`. The framework does not ship them; it
# is up to the operator to migrate them.

set -u

# Resolve the current working tree's root. The hook fires inside a
# git checkout — `git rev-parse --show-toplevel` should always
# succeed; the `2>/dev/null` is defensive against odd worktree
# states.
worktree=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$worktree" ]; then
  exit 0
fi

# Is this repo Claude-Code-aware? A worktree that `git worktree add`
# just created has no `.claude/` of its own — the directory is
# gitignored by convention — so fall back to asking the main checkout,
# reached through the shared git dir.
claude_aware=0
[ -d "$worktree/.claude" ] && claude_aware=1

if [ "$claude_aware" -eq 0 ]; then
  common_dir=$(git rev-parse --git-common-dir 2>/dev/null || true)
  if [ -n "$common_dir" ]; then
    case "$common_dir" in
      /*) ;;
      *) common_dir="$PWD/$common_dir" ;;
    esac
    # `<super>/.git/modules/<name>` in a submodule — no main checkout
    # above it, so only a real `.git` common dir answers here.
    main_root=""
    case "$common_dir" in
      */.git) main_root=$(cd "$(dirname "$common_dir")" 2>/dev/null && pwd) || main_root="" ;;
    esac
    if [ -n "$main_root" ] && [ -d "$main_root/.claude" ]; then
      claude_aware=1
    fi
  fi
fi

# Sandbox-allowlist helper
if [ -x "$HOME/.claude/scripts/sandbox-add-project-root.sh" ] \
    && [ "$claude_aware" -eq 1 ]; then
  "$HOME/.claude/scripts/sandbox-add-project-root.sh" 2>/dev/null || true
fi

exit 0
