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
# sandbox-status-line.sh — Claude Code statusLine helper.
#
# Renders one line for the terminal footer that always shows whether
# Claude Code's filesystem/network sandbox is active in the current
# session, so a session that is *not* sandboxed cannot drift
# unnoticed for hours. Alongside the sandbox tag (green `[sandbox]` /
# yellow `[sandbox-auto]` / bold-red `[NO SANDBOX]`) it carries what
# tells several sessions apart across worktrees and repos — which
# project, which branch, which PR:
#
#   [sandbox]  <folder>   | <branch><dirty><ahead/behind>
#                         | #<PR> <title>
#                         | <model>
#
# - Folder name is the *project root* — the main worktree, not the
#   current directory — colour-coded by a stable hash of its basename,
#   so a repo keeps the same colour across sessions, subdirectories and
#   worktrees.
# - Inside a linked git worktree it renders `<repo>/<worktree>` with each
#   half hash-coloured independently. The worktree half drops the
#   redundant repo prefix the worktree directory conventionally carries
#   (`<repo>.<slug>` from worktree managers, `<repo>-<slug>` from a plain
#   `git worktree add`), so `repo.gh-1234` shows as `repo/gh-1234`. A
#   Claude Code worktree (`<source>/.claude/worktrees/<name>`) is a linked
#   worktree too and renders the same way.
# - Git segment is local-only (no network): branch, dirty marker
#   (`-uno` skips untracked-file scan), ahead/behind vs upstream from
#   cached refs.
# - PR segment runs `gh pr view` once per branch with a 5-min success
#   / 60-s miss cache under `${XDG_CACHE_HOME:-~/.cache}/claude-statusline/`.
#   Silent when `gh` is missing, unauthenticated, or the branch has no
#   PR. Wrapped in a portable `timeout` (with a `perl -e 'alarm'`
#   fallback for systems where neither `timeout` nor `gtimeout` is
#   installed) so a hung gh call cannot stall every status-line render.
# - Yellow `[sandbox-auto]` lights up when the active settings set
#   `.sandbox.autoAllowBashIfSandboxed: true` — that flag widens blast
#   radius (bash inside the sandbox skips the per-call allow prompt)
#   and is worth surfacing distinctly from plain `[sandbox]`.
#
# Sandbox tag reads `.sandbox.enabled` from the active settings,
# walked in Claude Code's standard precedence order (most-specific
# first):
#   1. <project-root>/.claude/settings.local.json
#   2. <project-root>/.claude/settings.json
#   3. ~/.claude/settings.local.json
#   4. ~/.claude/settings.json
# First file with `.sandbox.enabled` set (true *or* false) wins. The
# `/sandbox` slash-command toggle persists to project-scope
# `settings.local.json`, so reading that file is what makes the prefix
# update after an in-session toggle. CLI flags like
# `--bypass-permissions` are still not visible here — pair with
# `sandbox-bypass-warn.sh` for per-call signal.
#
# `<project-root>` is the **main worktree**, not `<cwd>`: Claude Code
# resolves project scope through the shared git directory, so a session
# started in a linked worktree reads — and `/sandbox` writes — the main
# checkout's `.claude/settings.local.json`. Reading `<cwd>/.claude/`
# instead found no `enabled` key in a worktree (the per-worktree file
# carries only the sandbox filesystem allowlist), fell through to
# user scope, and painted a green `[sandbox]` on a session that was
# running with no sandbox at all. Resolving the project root the way the
# harness does also fixes the same silent fall-through for a session
# started in a subdirectory of the repo.
#
# Wiring (user-scope):
#
#   {
#     "statusLine": {
#       "type": "command",
#       "command": "~/.claude/scripts/sandbox-status-line.sh"
#     }
#   }
#
# See `docs/setup/secure-agent-setup.md` → "Sandbox-state status line" for
# the install steps. This is the only status line Magpie ships and the
# one the setup skill installs. A minimal sandbox-tag-only variant
# existed alongside it until 0.2.0 and was dropped: every segment here
# degrades to silence on its own when its input is missing, so the
# small version was this script with less to say and a second file to
# keep correct.

set -u

input=$(cat)

# ---------------------------------------------------------------------------
# 0. Parse stdin once — pull out cwd and model display name.
# ---------------------------------------------------------------------------
parsed=$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(); sys.exit(0)
cwd = d.get("cwd") or (d.get("workspace") or {}).get("current_dir") or ""
m = d.get("model") or {}
model = m.get("display_name") or m.get("id") or ""
print(f"{cwd}\t{model}")' 2>/dev/null)
cwd=${parsed%%$'\t'*}
model=${parsed#*$'\t'}
[ -z "$cwd" ] && cwd="$PWD"

# ---------------------------------------------------------------------------
# 0b. Git context — resolved once, reused by every segment below.
#
#     git_dir       this working tree's own git dir. For a linked worktree
#                   that is <main>/.git/worktrees/<name>, which is where its
#                   HEAD lives.
#     worktree_root the working tree $cwd sits in.
#     project_root  what Claude Code treats as PROJECT SCOPE: the main
#                   worktree (the parent of the *common* git dir), which is
#                   the same directory for the main checkout and for every
#                   linked worktree of it.
#
#     `--path-format=absolute` (git >= 2.31) answers all three in one call;
#     older git gets one call per value plus manual absolutisation (git
#     answers `.git` in a working-tree root and an absolute path from a
#     subdirectory).
# ---------------------------------------------------------------------------
git_dir=""
common_dir=""
worktree_root=""

if git_ctx=$(git -C "$cwd" rev-parse --path-format=absolute \
        --git-dir --git-common-dir --show-toplevel 2>/dev/null); then
    { read -r git_dir; read -r common_dir; read -r worktree_root; } <<EOF
$git_ctx
EOF
else
    git_dir=$(git -C "$cwd" rev-parse --git-dir 2>/dev/null) || git_dir=""
    common_dir=$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null) || common_dir=""
    worktree_root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || worktree_root=""
    [[ -n "$git_dir" && "$git_dir" != /* ]] && git_dir="$cwd/$git_dir"
    [[ -n "$common_dir" && "$common_dir" != /* ]] && common_dir="$cwd/$common_dir"
fi

# Only a common dir that *is* a `.git` directory has a working tree
# above it. Inside a submodule it is `<super>/.git/modules/<name>`,
# whose parent is no project root at all — there, and under an explicit
# GIT_DIR, fall back to the working tree we are standing in.
project_root=""
case "$common_dir" in
    */.git) project_root=$(cd "$(dirname "$common_dir")" 2>/dev/null && pwd) || project_root="" ;;
esac
[ -z "$project_root" ] && project_root="$worktree_root"
[ -z "$project_root" ] && project_root="$cwd"

# ---------------------------------------------------------------------------
# 1. Folder (colour-coded by hash)
#    The repo — the project root — is the stable half, so it keeps its colour
#    across subdirectories and across every worktree of it. In a linked
#    worktree we render "<repo>/<worktree>" with each half hash-coloured
#    independently, dropping the repo prefix a worktree directory
#    conventionally carries: "<repo>.<slug>" (worktree managers),
#    "<repo>-<slug>" (plain `git worktree add`), "<repo>_<slug>".
# ---------------------------------------------------------------------------
folder=$(basename "$project_root")
worktree_name=""
if [ -n "$worktree_root" ] && [ "$worktree_root" != "$project_root" ]; then
    worktree_name=$(basename "$worktree_root")
    case "$worktree_name" in
        "$folder"[._-]*)
            stripped="${worktree_name#"$folder"?}"
            [ -n "$stripped" ] && worktree_name="$stripped"
            ;;
    esac
elif [[ "$cwd" == *"/.claude/worktrees/"* ]]; then
    # No git on PATH (or not a working tree): fall back to reading the
    # Claude Code worktree layout straight off the path.
    worktree_name=$(basename "$cwd")
    folder=$(basename "${cwd%%/.claude/worktrees/*}")
fi

palette=(33 39 45 51 75 81 87 105 111 117 123 141 147 153 159 165 171 177 183 189 195 201 207 213 219 220 226 214 208 202 196 199 203 209 215 221 227 154 118 82 46 47 48 49 50 84 120 156 190 228)
n=${#palette[@]}
idx=$(printf '%s' "$folder" | cksum | awk '{print $1}')
color=${palette[$((idx % n))]}
folder_out=$(printf '\033[38;5;%dm%s\033[0m' "$color" "$folder")

if [ -n "$worktree_name" ]; then
    wt_idx=$(printf '%s' "$worktree_name" | cksum | awk '{print $1}')
    wt_color=${palette[$((wt_idx % n))]}
    wt_out=$(printf '\033[38;5;%dm%s\033[0m' "$wt_color" "$worktree_name")
    folder_out="$folder_out/$wt_out"
fi

# ---------------------------------------------------------------------------
# 2. Git status
# ---------------------------------------------------------------------------
git_info=""
branch=""

if [ -n "$git_dir" ]; then
    head_file="$git_dir/HEAD"
    if [ -f "$head_file" ]; then
        head_content=$(cat "$head_file")
        if [[ "$head_content" == ref:* ]]; then
            branch="${head_content#ref: refs/heads/}"
        else
            branch="${head_content:0:7} (detached)"
        fi
    fi

    dirty=""
    if [ -n "$(git -C "$cwd" status --porcelain --no-renames -uno 2>/dev/null)" ]; then
        dirty="*"
    fi

    ahead_behind=""
    if upstream=$(git -C "$cwd" rev-parse --abbrev-ref "@{u}" 2>/dev/null); then
        if counts=$(git -C "$cwd" rev-list --left-right --count "HEAD...@{u}" 2>/dev/null); then
            ahead=$(printf '%s' "$counts" | awk '{print $1}')
            behind=$(printf '%s' "$counts" | awk '{print $2}')
            [ "${ahead:-0}" -gt 0 ] 2>/dev/null && ahead_behind="+$ahead"
            [ "${behind:-0}" -gt 0 ] 2>/dev/null && ahead_behind="${ahead_behind}-$behind"
            [ -n "$ahead_behind" ] && ahead_behind=" $ahead_behind"
        fi
    fi

    [ -n "$branch" ] && git_info=" | $branch$dirty$ahead_behind"
fi

# ---------------------------------------------------------------------------
# 3. PR title (cached per repo+branch)
# ---------------------------------------------------------------------------
pr_info=""
if [ -n "$branch" ] && [[ "$branch" != *"(detached)" ]] && command -v gh >/dev/null 2>&1; then
    cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/claude-statusline"
    mkdir -p "$cache_dir" 2>/dev/null
    repo_root="${worktree_root:-$project_root}"
    if [ -n "$repo_root" ]; then
        safe_key=$(printf '%s/%s' "$repo_root" "$branch" | tr '/ ' '__')
        cache_file="$cache_dir/pr_${safe_key}"

        # TTL: 5 min on success, 60 s on miss so we re-probe quickly after
        # `gh auth login`, a PR open, or gh being installed.
        now=$(date +%s)
        cache_mtime=0
        # GNU stat (Linux) first, BSD stat (macOS) fallback. Order
        # matters: on Linux, BSD's `-f %m` is misread as "filesystem
        # mode" and stat exits 0 with multi-line garbage on stdout
        # that breaks the arithmetic on the next line under `set -u`.
        [ -f "$cache_file" ] && cache_mtime=$(stat -c %Y "$cache_file" 2>/dev/null || stat -f %m "$cache_file" 2>/dev/null || echo 0)
        age=$(( now - cache_mtime ))
        ttl=300
        [ -s "$cache_file" ] || ttl=60

        if [ "$age" -ge "$ttl" ]; then
            # Portable timeout wrapper: prefer `timeout` / `gtimeout`; fall
            # back to perl (present on macOS by default; plain `timeout` is
            # not, which silently broke this segment previously).
            if command -v timeout >/dev/null 2>&1; then
                tmo=(timeout 3)
            elif command -v gtimeout >/dev/null 2>&1; then
                tmo=(gtimeout 3)
            else
                tmo=(perl -e 'alarm shift; exec @ARGV' 3)
            fi
            pr_raw=$(cd "$cwd" && "${tmo[@]}" gh pr view --json number,title --jq '"#\(.number) \(.title)"' 2>/dev/null || true)
            printf '%s' "$pr_raw" > "$cache_file"
        else
            pr_raw=$(cat "$cache_file")
        fi

        if [ -n "$pr_raw" ]; then
            if [ "${#pr_raw}" -gt 55 ]; then
                pr_raw="${pr_raw:0:55}..."
            fi
            pr_info=" | $pr_raw"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 4. Sandbox state — green [sandbox] / yellow [sandbox-auto] / bold-red
#    [NO SANDBOX] suffix. Walks settings.local.json + settings.json in
#    project then user scope, where "project" is the main worktree
#    ($project_root above) — the scope Claude Code itself resolves, in a
#    linked worktree as much as in the main checkout. First file with
#    `.sandbox.enabled` set (true *or* false) wins — `/sandbox` writes to
#    project `settings.local.json`, so that is the file that flips when
#    the user toggles in-session. The yellow `[sandbox-auto]` variant
#    lights up when `.sandbox.autoAllowBashIfSandboxed` is true there —
#    it signals that bash commands inside the sandbox skip the per-call
#    allow prompt, which is a wider blast radius than vanilla sandbox.
# ---------------------------------------------------------------------------
sandbox=""
sandbox_auto=""
for f in \
    "${project_root:+$project_root/.claude/settings.local.json}" \
    "${project_root:+$project_root/.claude/settings.json}" \
    "$HOME/.claude/settings.local.json" \
    "$HOME/.claude/settings.json"; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    v=$(python3 -c 'import json,sys
try:
    s = json.load(open(sys.argv[1])).get("sandbox",{})
    e = s.get("enabled")
    a = s.get("autoAllowBashIfSandboxed")
    en = "true" if e is True else ("false" if e is False else "")
    au = "true" if a is True else ("false" if a is False else "")
    print(f"{en}\t{au}")
except Exception:
    pass' "$f" 2>/dev/null)
    en=${v%%$'\t'*}
    au=${v#*$'\t'}
    if [ -n "$en" ]; then
        sandbox="$en"
        sandbox_auto="$au"
        break
    fi
done

esc=$(printf '\033')
green="${esc}[1;32m"
yellow="${esc}[1;33m"
red="${esc}[1;31m"
reset="${esc}[0m"

if [ "$sandbox" = "true" ]; then
    if [ "$sandbox_auto" = "true" ]; then
        sandbox_tag="${yellow}[sandbox-auto]${reset}"
    else
        sandbox_tag="${green}[sandbox]${reset}"
    fi
else
    sandbox_tag="${red}[NO SANDBOX]${reset}"
fi

model_info=""
[ -n "$model" ] && model_info=" | $model"

# ---------------------------------------------------------------------------
# 5. Emit — sandbox tag leads, then folder / git / PR / model.
# ---------------------------------------------------------------------------
printf '%s %s%s%s%s' "$sandbox_tag" "$folder_out" "$git_info" "$pr_info" "$model_info"
