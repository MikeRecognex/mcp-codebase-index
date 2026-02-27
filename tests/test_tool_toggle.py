"""Tests for the tool toggling feature (--disabled-tools + TOML config)."""

import asyncio

import pytest

import mcp_codebase_index.server as srv


@pytest.fixture(autouse=True)
def _reset_toggle_state():
    """Reset the disabled-tools set and related state before each test."""
    srv._disabled_tools = set()
    yield
    srv._disabled_tools = set()


# ---------------------------------------------------------------------------
# _load_disabled_tools_from_config
# ---------------------------------------------------------------------------


class TestLoadDisabledToolsFromConfig:
    def test_no_config_file(self, tmp_path):
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == set()

    def test_valid_config(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text(
            'disabled_tools = ["search_codebase", "get_call_chain"]\n'
        )
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == {"search_codebase", "get_call_chain"}

    def test_empty_list(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text("disabled_tools = []\n")
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == set()

    def test_invalid_type_not_list(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text('disabled_tools = "search_codebase"\n')
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == set()

    def test_invalid_type_list_of_non_strings(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text("disabled_tools = [1, 2]\n")
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == set()

    def test_malformed_toml(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text("not valid toml [[[")
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == set()

    def test_missing_key(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text("[other]\nfoo = 1\n")
        result = srv._load_disabled_tools_from_config(str(tmp_path))
        assert result == set()


# ---------------------------------------------------------------------------
# _init_disabled_tools
# ---------------------------------------------------------------------------


class TestInitDisabledTools:
    def test_cli_only(self, tmp_path):
        srv._init_disabled_tools(["search_codebase"], project_root=str(tmp_path))
        assert srv._disabled_tools == {"search_codebase"}

    def test_config_only(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text(
            'disabled_tools = ["get_call_chain"]\n'
        )
        srv._init_disabled_tools(None, project_root=str(tmp_path))
        assert srv._disabled_tools == {"get_call_chain"}

    def test_union_of_cli_and_config(self, tmp_path):
        (tmp_path / ".mcp-codebase-index.toml").write_text(
            'disabled_tools = ["get_call_chain"]\n'
        )
        srv._init_disabled_tools(["search_codebase"], project_root=str(tmp_path))
        assert srv._disabled_tools == {"search_codebase", "get_call_chain"}

    def test_protected_tools_cannot_be_disabled(self, tmp_path):
        srv._init_disabled_tools(["reindex", "get_usage_stats", "search_codebase"],
                                 project_root=str(tmp_path))
        assert "reindex" not in srv._disabled_tools
        assert "get_usage_stats" not in srv._disabled_tools
        assert "search_codebase" in srv._disabled_tools

    def test_unknown_tools_ignored(self, tmp_path):
        srv._init_disabled_tools(["not_a_real_tool", "search_codebase"],
                                 project_root=str(tmp_path))
        assert "not_a_real_tool" not in srv._disabled_tools
        assert "search_codebase" in srv._disabled_tools

    def test_empty_cli_list(self, tmp_path):
        srv._init_disabled_tools([], project_root=str(tmp_path))
        assert srv._disabled_tools == set()

    def test_none_cli_no_config(self, tmp_path):
        srv._init_disabled_tools(None, project_root=str(tmp_path))
        assert srv._disabled_tools == set()


# ---------------------------------------------------------------------------
# list_tools filtering
# ---------------------------------------------------------------------------


class TestListToolsFiltering:
    def test_no_disabled_returns_all(self):
        srv._disabled_tools = set()
        tools = asyncio.run(srv.list_tools())
        assert len(tools) == len(srv.TOOLS)

    def test_disabled_tools_excluded(self):
        srv._disabled_tools = {"search_codebase", "get_call_chain"}
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "search_codebase" not in names
        assert "get_call_chain" not in names
        assert len(tools) == len(srv.TOOLS) - 2

    def test_protected_always_present(self):
        srv._disabled_tools = {"search_codebase"}
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "reindex" in names
        assert "get_usage_stats" in names


# ---------------------------------------------------------------------------
# call_tool guard
# ---------------------------------------------------------------------------


class TestCallToolGuard:
    def test_disabled_tool_returns_error(self):
        srv._disabled_tools = {"search_codebase"}
        result = asyncio.run(srv.call_tool("search_codebase", {"pattern": "foo"}))
        assert len(result) == 1
        assert "disabled" in result[0].text
        assert "search_codebase" in result[0].text

    def test_disabled_tool_not_counted(self):
        srv._tool_call_counts.clear()
        srv._disabled_tools = {"search_codebase"}
        asyncio.run(srv.call_tool("search_codebase", {"pattern": "foo"}))
        assert "search_codebase" not in srv._tool_call_counts

    def test_enabled_tool_not_blocked(self, monkeypatch):
        """An enabled tool should proceed past the guard (we mock _ensure_index)."""
        srv._disabled_tools = set()
        srv._tool_call_counts.clear()
        # Prevent actual indexing
        monkeypatch.setattr(srv, "_ensure_index", lambda: None)
        monkeypatch.setattr(srv, "_maybe_incremental_update", lambda: None)
        monkeypatch.setattr(srv, "_query_fns", {
            "get_project_summary": lambda: "mock summary",
        })
        result = asyncio.run(srv.call_tool("get_project_summary", {}))
        assert result[0].text == "mock summary"
        assert srv._tool_call_counts.get("get_project_summary") == 1
