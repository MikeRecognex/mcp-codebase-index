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

"""Git change detection for incremental re-indexing."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


@dataclass
class GitChangeSet:
    """Set of files changed since a given git ref."""

    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.modified and not self.added and not self.deleted


def is_git_repo(root_path: str) -> bool:
    """Check if the given path is inside a git work tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_head_commit(root_path: str) -> str | None:
    """Get the current HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_changed_files(root_path: str, since_ref: str | None) -> GitChangeSet:
    """Get files changed since a given git ref.

    Combines committed changes (since_ref..HEAD), staged changes,
    unstaged changes, and untracked files into a single GitChangeSet.
    """
    if since_ref is None:
        return GitChangeSet()

    modified: set[str] = set()
    added: set[str] = set()
    deleted: set[str] = set()

    # 1. Committed changes since the ref
    _parse_diff_output(root_path, ["git", "diff", "--name-status", since_ref, "HEAD"],
                       modified, added, deleted)

    # 2. Unstaged changes
    _parse_diff_output(root_path, ["git", "diff", "--name-status"],
                       modified, added, deleted)

    # 3. Staged changes
    _parse_diff_output(root_path, ["git", "diff", "--name-status", "--cached"],
                       modified, added, deleted)

    # 4. Untracked files
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
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
