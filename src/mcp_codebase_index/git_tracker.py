# mcp-codebase-index - Structural codebase indexer with MCP server
# Copyright (C) 2026 Michael Doyle
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing available. See COMMERCIAL-LICENSE.md for details.

"""Git change detection for incremental re-indexing.

On Windows, Python .exe console_scripts (installed via pip) often inherit
a reduced PATH that does not include ``git``.  To avoid hangs caused by
``subprocess.run(["git", ...])`` waiting for a missing binary, the
hot-path helpers :func:`is_git_repo` and :func:`get_head_commit` use
direct filesystem reads of ``.git/`` instead of shelling out.

A resolved path to ``git`` is still needed for diff-based incremental
updates; :func:`_find_git` locates the binary once at import time.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field


_COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


@dataclass
class GitChangeSet:
    """Set of files changed since a given git ref."""

    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.modified and not self.added and not self.deleted


# ---------------------------------------------------------------------------
# Git binary resolution (needed for diff/ls-files calls)
# ---------------------------------------------------------------------------

def _find_git() -> str:
    """Return the absolute path to a ``git`` binary.

    ``shutil.which`` may fail inside pip-installed ``.exe`` wrappers on
    Windows because they inherit a minimal PATH.  Fall back to well-known
    install locations before giving up.
    """
    found = shutil.which("git")
    if found:
        return found
    for candidate in [
        os.path.expandvars(r"%ProgramFiles%\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Git\cmd\git.exe"),
        r"C:\Program Files\Git\cmd\git.exe",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return "git"  # last resort – let subprocess raise FileNotFoundError


_GIT_CMD: str = _find_git()


# ---------------------------------------------------------------------------
# Filesystem-based helpers (no subprocess, no PATH dependency)
# ---------------------------------------------------------------------------

def _resolve_git_dir(path: str) -> str | None:
    """Find the ``.git`` directory for a working tree path.

    Walks up from *path* looking for a ``.git`` entry.  Supports both
    regular repositories (``.git/`` directory) and worktrees / submodules
    (``.git`` file containing ``gitdir: <path>``).

    Returns the resolved git directory path, or ``None``.
    """
    path = os.path.abspath(path)
    while True:
        dot_git = os.path.join(path, ".git")
        if os.path.isdir(dot_git):
            return dot_git
        if os.path.isfile(dot_git):
            try:
                with open(dot_git, "r") as f:
                    content = f.read().strip()
                if content.startswith("gitdir: "):
                    git_dir = content[8:]
                    if not os.path.isabs(git_dir):
                        git_dir = os.path.normpath(os.path.join(path, git_dir))
                    if os.path.isdir(git_dir):
                        return git_dir
            except (OSError, IOError):
                pass
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def is_git_repo(root_path: str) -> bool:
    """Check if *root_path* is inside a git work tree.

    Uses a filesystem walk (looking for a ``.git`` entry) instead of
    ``git rev-parse`` so that it works reliably inside pip-installed
    ``.exe`` wrappers on Windows where ``git`` may not be on PATH.

    Supports regular repos, worktrees, and submodules (where ``.git``
    is a file containing ``gitdir: <path>``).
    """
    return _resolve_git_dir(root_path) is not None


def get_head_commit(root_path: str) -> str | None:
    """Return the current HEAD commit hash by reading ``.git/`` directly.

    Avoids shelling out to ``git rev-parse HEAD`` which can hang on
    Windows when ``git`` is not on PATH.  Supports both SHA-1 (40 hex)
    and SHA-256 (64 hex) object IDs.
    """
    git_dir = _resolve_git_dir(root_path)
    if git_dir is None:
        return None
    head_file = os.path.join(git_dir, "HEAD")
    try:
        with open(head_file, "r") as f:
            content = f.read().strip()
        if content.startswith("ref: "):
            # Symbolic ref → resolve to a commit hash
            ref_path = os.path.join(git_dir, content[5:])
            if os.path.isfile(ref_path):
                with open(ref_path, "r") as f:
                    return f.read().strip()
            # Ref may be packed
            packed = os.path.join(git_dir, "packed-refs")
            if os.path.isfile(packed):
                ref_name = content[5:]
                with open(packed, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#"):
                            continue
                        parts = line.split(" ", 1)
                        if len(parts) == 2 and parts[1] == ref_name:
                            return parts[0]
            return None
        # Detached HEAD – content is the hash itself (SHA-1 or SHA-256)
        return content if _COMMIT_HASH_RE.match(content) else None
    except (OSError, IOError):
        return None


# ---------------------------------------------------------------------------
# Subprocess-based helpers (use resolved _GIT_CMD)
# ---------------------------------------------------------------------------

def get_changed_files(
    root_path: str,
    since_ref: str | None,
    *,
    skip_committed: bool = False,
) -> GitChangeSet:
    """Get files changed since a given git ref.

    Combines committed changes (since_ref..HEAD), staged changes,
    unstaged changes, and untracked files into a single GitChangeSet.

    When *skip_committed* is ``True``, the ``since_ref..HEAD`` diff is
    skipped (useful when HEAD hasn't moved), but the working tree is
    still checked for unstaged, staged, and untracked changes.
    """
    if since_ref is None:
        return GitChangeSet()

    modified: set[str] = set()
    added: set[str] = set()
    deleted: set[str] = set()

    # 1. Committed changes since the ref
    if not skip_committed:
        _parse_diff_output(root_path, [_GIT_CMD, "diff", "--name-status", since_ref, "HEAD"],
                           modified, added, deleted)

    # 2. Unstaged changes
    _parse_diff_output(root_path, [_GIT_CMD, "diff", "--name-status"],
                       modified, added, deleted)

    # 3. Staged changes
    _parse_diff_output(root_path, [_GIT_CMD, "diff", "--name-status", "--cached"],
                       modified, added, deleted)

    # 4. Untracked files
    try:
        result = subprocess.run(
            [_GIT_CMD, "ls-files", "--others", "--exclude-standard"],
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                path = line.strip()
                if path:
                    added.add(path)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Resolve overlaps: file in both added and deleted → modified
    overlap = added & deleted
    modified |= overlap
    added -= overlap
    deleted -= overlap

    return GitChangeSet(
        modified=sorted(modified),
        added=sorted(added),
        deleted=sorted(deleted),
    )


def _parse_diff_output(
    root_path: str,
    cmd: list[str],
    modified: set[str],
    added: set[str],
    deleted: set[str],
) -> None:
    """Parse git diff --name-status output into modified/added/deleted sets."""
    try:
        result = subprocess.run(
            cmd,
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[1]

        if status == "M":
            modified.add(path)
        elif status == "A":
            added.add(path)
        elif status == "D":
            deleted.add(path)
        elif status.startswith("R"):
            # Rename: delete old path, add new path
            deleted.add(path)
            if len(parts) >= 3:
                added.add(parts[2])
