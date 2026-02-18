"""Tests for the get_usage_stats session metrics."""

import time

import pytest


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Reset server module-level state before each test."""
    import mcp_codebase_index.server as srv

    srv._session_start = time.time()
    srv._tool_call_counts.clear()
    srv._total_chars_returned = 0
    srv._indexer = None
    srv._query_fns = None
    yield
    srv._tool_call_counts.clear()
    srv._total_chars_returned = 0


class TestFormatDuration:
    def test_seconds(self):
        from mcp_codebase_index.server import _format_duration

        assert _format_duration(45) == "45s"

    def test_minutes(self):
        from mcp_codebase_index.server import _format_duration

        assert _format_duration(125) == "2m 5s"

    def test_hours(self):
        from mcp_codebase_index.server import _format_duration

        assert _format_duration(3725) == "1h 2m"


class TestFormatUsageStats:
    def test_empty_session(self):
        from mcp_codebase_index.server import _format_usage_stats

        result = _format_usage_stats()
        assert "Total queries: 0" in result
        assert "Total chars returned: 0" in result

    def test_with_tool_calls(self):
        import mcp_codebase_index.server as srv

        srv._tool_call_counts["find_symbol"] = 5
        srv._tool_call_counts["get_function_source"] = 3
        srv._total_chars_returned = 1234

        result = srv._format_usage_stats()
        assert "Total queries: 8" in result
        assert "find_symbol: 5" in result
        assert "get_function_source: 3" in result
        assert "Total chars returned: 1,234" in result

    def test_usage_stats_call_excluded_from_query_count(self):
        import mcp_codebase_index.server as srv

        srv._tool_call_counts["find_symbol"] = 3
        srv._tool_call_counts["get_usage_stats"] = 2

        result = srv._format_usage_stats()
        assert "Total queries: 3" in result
        # get_usage_stats should not appear in the per-tool breakdown
        assert "get_usage_stats" not in result

    def test_with_indexed_project(self, tmp_path):
        import mcp_codebase_index.server as srv
        from mcp_codebase_index.project_indexer import ProjectIndexer

        # Create a project with enough source to exceed returned chars
        (tmp_path / "main.py").write_text("def hello():\n    return 'world'\n" * 100)
        (tmp_path / "utils.py").write_text("def helper():\n    return 42\n" * 100)

        indexer = ProjectIndexer(str(tmp_path), include_patterns=["**/*.py"])
        indexer.index()
        srv._indexer = indexer

        srv._tool_call_counts["find_symbol"] = 5
        srv._total_chars_returned = 200

        result = srv._format_usage_stats()
        assert "Total source in index:" in result
        assert "Estimated token savings:" in result

    def test_token_savings_calculation(self, tmp_path):
        import mcp_codebase_index.server as srv
        from mcp_codebase_index.project_indexer import ProjectIndexer

        # Create a project with known size
        (tmp_path / "big.py").write_text("x = 1\n" * 1000)  # ~6000 chars

        indexer = ProjectIndexer(str(tmp_path), include_patterns=["**/*.py"])
        indexer.index()
        srv._indexer = indexer

        srv._tool_call_counts["find_symbol"] = 10
        srv._total_chars_returned = 500

        result = srv._format_usage_stats()
        assert "Estimated without indexer:" in result
        assert "Estimated with indexer:" in result
        # 500 chars returned vs 6000 * 10 = 60000 naive
        assert "tokens" in result

    def test_no_savings_section_without_index(self):
        import mcp_codebase_index.server as srv

        srv._tool_call_counts["find_symbol"] = 3
        srv._total_chars_returned = 100

        result = srv._format_usage_stats()
        assert "Estimated token savings:" not in result
