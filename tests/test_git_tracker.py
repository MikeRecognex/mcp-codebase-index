"""Unit tests for git_tracker module."""

import os
import tempfile

from unittest.mock import patch, MagicMock

from mcp_codebase_index.git_tracker import (
    is_git_repo,
    get_head_commit,
    get_changed_files,
    GitChangeSet,
    _find_git,
    _resolve_git_dir,
    _COMMIT_HASH_RE,
)


_SHA1_HASH = "a" * 40
_SHA256_HASH = "b" * 64


# ---------------------------------------------------------------------------
# _resolve_git_dir
# ---------------------------------------------------------------------------


class TestResolveGitDir:
    def test_returns_git_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _resolve_git_dir(str(tmp_path)) == str(tmp_path / ".git")

    def test_walks_up_from_subdirectory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        assert _resolve_git_dir(str(child)) == str(tmp_path / ".git")

    def test_follows_git_file_absolute_path(self, tmp_path):
        """Worktree/submodule: .git is a file with gitdir: <abs-path>."""
        real_git_dir = tmp_path / "real_git_dir"
        real_git_dir.mkdir()
        (tmp_path / "worktree" / ".git").parent.mkdir(parents=True)
        (tmp_path / "worktree" / ".git").write_text(
            f"gitdir: {real_git_dir}\n"
        )
        assert _resolve_git_dir(str(tmp_path / "worktree")) == str(real_git_dir)

    def test_follows_git_file_relative_path(self, tmp_path):
        """Worktree/submodule: .git file with a relative gitdir."""
        real_git_dir = tmp_path / ".git" / "worktrees" / "feature"
        real_git_dir.mkdir(parents=True)
        wt = tmp_path / "worktrees" / "feature"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(
            "gitdir: ../../.git/worktrees/feature\n"
        )
        assert os.path.normpath(
            _resolve_git_dir(str(wt))
        ) == os.path.normpath(str(real_git_dir))

    def test_returns_none_for_non_git(self, tmp_path):
        assert _resolve_git_dir(str(tmp_path)) is None

    def test_ignores_invalid_git_file(self, tmp_path):
        """A .git file that doesn't start with 'gitdir: ' is ignored."""
        (tmp_path / ".git").write_text("garbage\n")
        assert _resolve_git_dir(str(tmp_path)) is None

    def test_ignores_git_file_pointing_to_missing_dir(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /nonexistent/path\n")
        assert _resolve_git_dir(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# is_git_repo  (filesystem-based, no subprocess)
# ---------------------------------------------------------------------------


class TestIsGitRepo:
    def test_returns_true_for_git_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert is_git_repo(str(tmp_path)) is True

    def test_returns_true_for_nested_path(self, tmp_path):
        (tmp_path / ".git").mkdir()
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        assert is_git_repo(str(child)) is True

    def test_returns_true_for_worktree_git_file(self, tmp_path):
        real_git_dir = tmp_path / "real_git_dir"
        real_git_dir.mkdir()
        (tmp_path / "worktree").mkdir()
        (tmp_path / "worktree" / ".git").write_text(
            f"gitdir: {real_git_dir}\n"
        )
        assert is_git_repo(str(tmp_path / "worktree")) is True

    def test_returns_false_for_non_git(self, tmp_path):
        assert is_git_repo(str(tmp_path)) is False

    def test_returns_false_for_empty_path(self):
        with tempfile.TemporaryDirectory() as d:
            assert is_git_repo(d) is False


# ---------------------------------------------------------------------------
# get_head_commit  (filesystem-based, no subprocess)
# ---------------------------------------------------------------------------


class TestGetHeadCommit:
    def test_returns_commit_from_symbolic_ref(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        refs = git_dir / "refs" / "heads"
        refs.mkdir(parents=True)
        (refs / "main").write_text(_SHA1_HASH + "\n")

        assert get_head_commit(str(tmp_path)) == _SHA1_HASH

    def test_returns_commit_from_detached_head_sha1(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(_SHA1_HASH + "\n")

        assert get_head_commit(str(tmp_path)) == _SHA1_HASH

    def test_returns_commit_from_detached_head_sha256(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(_SHA256_HASH + "\n")

        assert get_head_commit(str(tmp_path)) == _SHA256_HASH

    def test_returns_commit_from_packed_refs(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        # No loose ref file — only packed-refs
        (git_dir / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            f"{_SHA1_HASH} refs/heads/main\n"
        )

        assert get_head_commit(str(tmp_path)) == _SHA1_HASH

    def test_returns_commit_from_worktree_git_file(self, tmp_path):
        """get_head_commit works when .git is a file (worktree/submodule)."""
        real_git_dir = tmp_path / "real_git_dir"
        real_git_dir.mkdir()
        (real_git_dir / "HEAD").write_text(_SHA1_HASH + "\n")

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {real_git_dir}\n")

        assert get_head_commit(str(wt)) == _SHA1_HASH

    def test_returns_commit_from_subdirectory(self, tmp_path):
        """PROJECT_ROOT is a subdirectory of the repo."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(_SHA1_HASH + "\n")
        sub = tmp_path / "packages" / "core"
        sub.mkdir(parents=True)

        assert get_head_commit(str(sub)) == _SHA1_HASH

    def test_returns_none_when_no_git_dir(self, tmp_path):
        assert get_head_commit(str(tmp_path)) is None

    def test_returns_none_when_ref_missing(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/nonexistent\n")

        assert get_head_commit(str(tmp_path)) is None

    def test_returns_none_for_invalid_detached_head(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("not-a-hash\n")

        assert get_head_commit(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# _COMMIT_HASH_RE
# ---------------------------------------------------------------------------


class TestCommitHashRe:
    def test_matches_sha1(self):
        assert _COMMIT_HASH_RE.match(_SHA1_HASH)

    def test_matches_sha256(self):
        assert _COMMIT_HASH_RE.match(_SHA256_HASH)

    def test_rejects_short_hash(self):
        assert _COMMIT_HASH_RE.match("abc123") is None

    def test_rejects_non_hex(self):
        assert _COMMIT_HASH_RE.match("g" * 40) is None

    def test_rejects_50_chars(self):
        assert _COMMIT_HASH_RE.match("a" * 50) is None


# ---------------------------------------------------------------------------
# _find_git
# ---------------------------------------------------------------------------


class TestFindGit:
    def test_returns_string(self):
        assert isinstance(_find_git(), str)

    def test_finds_git_via_shutil_which(self):
        with patch("mcp_codebase_index.git_tracker.shutil.which", return_value="/usr/bin/git"):
            assert _find_git() == "/usr/bin/git"

    def test_falls_back_when_which_returns_none(self):
        with patch("mcp_codebase_index.git_tracker.shutil.which", return_value=None), \
             patch("mcp_codebase_index.git_tracker.os.path.isfile", return_value=False):
            assert _find_git() == "git"


# ---------------------------------------------------------------------------
# get_changed_files  (subprocess-based, uses _GIT_CMD)
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    def test_returns_empty_when_since_ref_is_none(self):
        changeset = get_changed_files("/some/path", None)
        assert changeset.is_empty

    def test_parses_modified_files(self):
        def mock_run(cmd, **kwargs):
            if cmd[1:3] == ["diff", "--name-status"] and len(cmd) == 5:
                return MagicMock(returncode=0, stdout="M\tfoo.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "foo.py" in changeset.modified

    def test_parses_added_files(self):
        def mock_run(cmd, **kwargs):
            if cmd[1:3] == ["diff", "--name-status"] and len(cmd) == 5:
                return MagicMock(returncode=0, stdout="A\tnew_file.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "new_file.py" in changeset.added

    def test_parses_deleted_files(self):
        def mock_run(cmd, **kwargs):
            if cmd[1:3] == ["diff", "--name-status"] and len(cmd) == 5:
                return MagicMock(returncode=0, stdout="D\told_file.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "old_file.py" in changeset.deleted

    def test_rename_handling(self):
        def mock_run(cmd, **kwargs):
            if cmd[1:3] == ["diff", "--name-status"] and len(cmd) == 5:
                return MagicMock(returncode=0, stdout="R100\told.py\tnew.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "old.py" in changeset.deleted
            assert "new.py" in changeset.added

    def test_overlap_resolution_added_and_deleted_becomes_modified(self):
        """If a file appears in both added and deleted, treat as modified."""
        def mock_run(cmd, **kwargs):
            if cmd[1:3] == ["diff", "--name-status"] and len(cmd) == 5:
                return MagicMock(returncode=0, stdout="D\toverlap.py\n")
            if cmd[1:] == ["diff", "--name-status"]:
                return MagicMock(returncode=0, stdout="A\toverlap.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "overlap.py" in changeset.modified
            assert "overlap.py" not in changeset.added
            assert "overlap.py" not in changeset.deleted

    def test_untracked_files_added(self):
        def mock_run(cmd, **kwargs):
            if "ls-files" in cmd:
                return MagicMock(returncode=0, stdout="untracked.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "untracked.py" in changeset.added

    def test_skip_committed_skips_ref_head_diff(self):
        """skip_committed=True should not call diff with since_ref..HEAD."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            get_changed_files("/some/path", "abc123", skip_committed=True)

        # No 5-arg diff command (the committed diff)
        for cmd in calls:
            assert not (cmd[1:3] == ["diff", "--name-status"] and len(cmd) == 5), \
                "skip_committed=True should not call since_ref..HEAD diff"

    def test_skip_committed_still_checks_working_tree(self):
        """skip_committed=True should still detect unstaged/staged/untracked."""
        def mock_run(cmd, **kwargs):
            # Unstaged: modified file
            if cmd[1:] == ["diff", "--name-status"]:
                return MagicMock(returncode=0, stdout="M\tunstaged.py\n")
            # Staged: added file
            if cmd[1:] == ["diff", "--name-status", "--cached"]:
                return MagicMock(returncode=0, stdout="A\tstaged.py\n")
            # Untracked
            if "ls-files" in cmd:
                return MagicMock(returncode=0, stdout="untracked.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("mcp_codebase_index.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123", skip_committed=True)
            assert "unstaged.py" in changeset.modified
            assert "staged.py" in changeset.added
            assert "untracked.py" in changeset.added

    def test_graceful_failure_git_not_found(self):
        with patch("mcp_codebase_index.git_tracker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            changeset = get_changed_files("/some/path", "abc123")
            assert changeset.is_empty


# ---------------------------------------------------------------------------
# GitChangeSet
# ---------------------------------------------------------------------------


class TestGitChangeSet:
    def test_is_empty_true(self):
        cs = GitChangeSet()
        assert cs.is_empty

    def test_is_empty_false_with_modified(self):
        cs = GitChangeSet(modified=["foo.py"])
        assert not cs.is_empty

    def test_is_empty_false_with_added(self):
        cs = GitChangeSet(added=["foo.py"])
        assert not cs.is_empty

    def test_is_empty_false_with_deleted(self):
        cs = GitChangeSet(deleted=["foo.py"])
        assert not cs.is_empty
