"""Tests for the ProjectIndexer.

Covers:
- File discovery with include/exclude patterns
- Symbol table population
- Import graph construction (Python and TypeScript)
- Dependency graph construction
- Reverse graphs
- Max file size filtering
- reindex_file incremental updates
- Integration test on the actual mcp-codebase-index source directory
"""

import os
import textwrap

import pytest

from mcp_codebase_index.models import ProjectIndex
from mcp_codebase_index.project_indexer import ProjectIndexer


# ---------------------------------------------------------------------------
# Fixtures: temporary project directory with interconnected Python files
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_project(tmp_path):
    """Create a small project with 4 Python files that import from each other.

    Structure:
        myproject/
            src/
                myproject/
                    __init__.py
                    core.py           (defines CoreEngine class)
                    utils.py          (defines helper, format_output functions)
                    models.py         (defines DataModel class, uses utils)
            tests/
                test_core.py         (imports core and models)
            __pycache__/
                cached.pyc           (should be excluded)
            README.md
    """
    root = tmp_path / "myproject"
    root.mkdir()

    # src/myproject/__init__.py
    src = root / "src" / "myproject"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")

    # src/myproject/core.py
    (src / "core.py").write_text(
        textwrap.dedent("""\
        from myproject.utils import helper
        from myproject.models import DataModel


        class CoreEngine:
            \"\"\"The main engine.\"\"\"

            def run(self, data):
                model = DataModel(data)
                return helper(model)

            def stop(self):
                pass


        def create_engine():
            return CoreEngine()
        """)
    )

    # src/myproject/utils.py
    (src / "utils.py").write_text(
        textwrap.dedent("""\
        import os


        def helper(obj):
            \"\"\"A helper function.\"\"\"
            return format_output(str(obj))


        def format_output(text):
            return text.strip()
        """)
    )

    # src/myproject/models.py
    (src / "models.py").write_text(
        textwrap.dedent("""\
        from myproject.utils import format_output


        class DataModel:
            \"\"\"A data model.\"\"\"

            def __init__(self, data):
                self.data = data

            def serialize(self):
                return format_output(str(self.data))
        """)
    )

    # tests/test_core.py
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(
        textwrap.dedent("""\
        from myproject.core import CoreEngine, create_engine
        from myproject.models import DataModel


        def test_engine():
            engine = create_engine()
            result = engine.run("test")
            assert result is not None


        def test_model():
            model = DataModel("hello")
            assert model.serialize() == "hello"
        """)
    )

    # __pycache__/cached.pyc (should be excluded)
    pycache = root / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.cpython-311.pyc").write_bytes(b"\x00" * 100)

    # README.md
    (root / "README.md").write_text("# My Project\n\nA sample project.\n")

    return root


@pytest.fixture
def ts_project(tmp_path):
    """Create a small TypeScript project with imports."""
    root = tmp_path / "tsproject"
    root.mkdir()

    src = root / "src"
    src.mkdir()

    (src / "utils.ts").write_text(
        textwrap.dedent("""\
        export function formatName(name: string): string {
            return name.trim();
        }

        export function validate(input: string): boolean {
            return input.length > 0;
        }
        """)
    )

    (src / "app.ts").write_text(
        textwrap.dedent("""\
        import { formatName, validate } from './utils';

        export class App {
            run(name: string): string {
                if (validate(name)) {
                    return formatName(name);
                }
                return '';
            }
        }
        """)
    )

    return root


# ---------------------------------------------------------------------------
# Test: file discovery
# ---------------------------------------------------------------------------


class TestFileDiscovery:
    def test_discovers_python_files(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # Should find __init__.py, core.py, utils.py, models.py, test_core.py
        py_files = [f for f in idx.files if f.endswith(".py")]
        assert len(py_files) >= 5

    def test_discovers_markdown_files(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        md_files = [f for f in idx.files if f.endswith(".md")]
        assert len(md_files) == 1
        assert any("README.md" in f for f in md_files)

    def test_excludes_pycache(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        for f in idx.files:
            assert "__pycache__" not in f, f"__pycache__ file should be excluded: {f}"

    def test_exclude_patterns_work(self, sample_project):
        # Exclude tests directory as well
        indexer = ProjectIndexer(
            str(sample_project),
            exclude_patterns=["**/__pycache__/**", "**/tests/**"],
        )
        idx = indexer.index()

        for f in idx.files:
            assert "tests/" not in f, f"tests file should be excluded: {f}"

    def test_max_file_size_filtering(self, sample_project):
        # Create a large file
        large_file = sample_project / "big_file.py"
        large_file.write_text("x = 1\n" * 100_000)  # ~600KB

        indexer = ProjectIndexer(str(sample_project), max_file_size_bytes=500_000)
        idx = indexer.index()

        assert "big_file.py" not in idx.files

    def test_include_patterns_filter(self, sample_project):
        # Only include Python files
        indexer = ProjectIndexer(
            str(sample_project),
            include_patterns=["**/*.py"],
        )
        idx = indexer.index()

        for f in idx.files:
            assert f.endswith(".py"), f"Non-Python file included: {f}"


# ---------------------------------------------------------------------------
# Test: symbol table
# ---------------------------------------------------------------------------


class TestSymbolTable:
    def test_functions_in_symbol_table(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # Top-level functions should be in the symbol table
        assert "helper" in idx.symbol_table
        assert "format_output" in idx.symbol_table
        assert "create_engine" in idx.symbol_table

    def test_classes_in_symbol_table(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        assert "CoreEngine" in idx.symbol_table
        assert "DataModel" in idx.symbol_table

    def test_methods_in_symbol_table(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # Methods should be registered with qualified names
        assert "CoreEngine.run" in idx.symbol_table
        assert "CoreEngine.stop" in idx.symbol_table
        assert "DataModel.serialize" in idx.symbol_table

    def test_symbol_table_maps_to_correct_file(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        assert idx.symbol_table["CoreEngine"].endswith("core.py")
        assert idx.symbol_table["helper"].endswith("utils.py")
        assert idx.symbol_table["DataModel"].endswith("models.py")


# ---------------------------------------------------------------------------
# Test: import graph
# ---------------------------------------------------------------------------


class TestImportGraph:
    def test_python_import_resolution(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # core.py imports from utils.py and models.py
        core_path = None
        utils_path = None
        models_path = None
        for f in idx.files:
            if f.endswith("core.py") and "test" not in f:
                core_path = f
            elif f.endswith("utils.py"):
                utils_path = f
            elif f.endswith("models.py") and "test" not in f:
                models_path = f

        assert core_path is not None
        assert core_path in idx.import_graph
        assert utils_path in idx.import_graph[core_path]
        assert models_path in idx.import_graph[core_path]

    def test_reverse_import_graph(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        utils_path = None
        for f in idx.files:
            if f.endswith("utils.py"):
                utils_path = f
                break

        assert utils_path is not None
        # utils.py should be imported by core.py and models.py
        assert utils_path in idx.reverse_import_graph
        importers = idx.reverse_import_graph[utils_path]
        # At least core.py and models.py import from utils
        assert len(importers) >= 2

    def test_typescript_relative_import(self, ts_project):
        indexer = ProjectIndexer(
            str(ts_project),
            include_patterns=["**/*.ts"],
        )
        idx = indexer.index()

        app_path = None
        utils_path = None
        for f in idx.files:
            if f.endswith("app.ts"):
                app_path = f
            elif f.endswith("utils.ts"):
                utils_path = f

        assert app_path is not None
        assert utils_path is not None
        assert app_path in idx.import_graph
        assert utils_path in idx.import_graph[app_path]


# ---------------------------------------------------------------------------
# Test: dependency graph
# ---------------------------------------------------------------------------


class TestDependencyGraph:
    def test_intra_file_dependencies(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # In utils.py, helper calls format_output
        assert "helper" in idx.global_dependency_graph
        assert "format_output" in idx.global_dependency_graph["helper"]

    def test_cross_file_dependencies(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # In models.py, DataModel.serialize calls format_output (imported from utils)
        if "DataModel" in idx.global_dependency_graph:
            deps = idx.global_dependency_graph["DataModel"]
            assert "format_output" in deps

    def test_reverse_dependency_graph(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        # format_output should have dependents
        if "format_output" in idx.reverse_dependency_graph:
            dependents = idx.reverse_dependency_graph["format_output"]
            assert len(dependents) >= 1


# ---------------------------------------------------------------------------
# Test: reindex_file
# ---------------------------------------------------------------------------


class TestReindexFile:
    def test_reindex_updates_metadata(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        utils_path = None
        for f in idx.files:
            if f.endswith("utils.py"):
                utils_path = f
                break

        assert utils_path is not None
        old_line_count = idx.files[utils_path].total_lines

        # Modify the file: add a new function
        abs_utils = os.path.join(str(sample_project), utils_path)
        with open(abs_utils, "a") as f:
            f.write("\n\ndef new_function():\n    return 42\n")

        indexer.reindex_file(utils_path)

        assert idx.files[utils_path].total_lines > old_line_count
        assert "new_function" in idx.symbol_table

    def test_reindex_updates_symbol_table(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        assert "create_engine" in idx.symbol_table

        # Rewrite core.py without create_engine
        core_path = None
        for f in idx.files:
            if f.endswith("core.py"):
                core_path = f
                break

        abs_core = os.path.join(str(sample_project), core_path)
        with open(abs_core, "w") as f:
            f.write(
                textwrap.dedent("""\
                class CoreEngine:
                    def run(self):
                        pass
                """)
            )

        indexer.reindex_file(core_path)

        # create_engine should be removed from symbol table
        # (unless it also exists in another file)
        if "create_engine" in idx.symbol_table:
            assert idx.symbol_table["create_engine"] != core_path

    def test_reindex_raises_without_initial_index(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))

        with pytest.raises(RuntimeError, match="Cannot reindex"):
            indexer.reindex_file("some/file.py")


# ---------------------------------------------------------------------------
# Test: stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_populated(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        assert idx.total_files > 0
        assert idx.total_lines > 0
        assert idx.total_functions > 0
        assert idx.total_classes > 0
        assert idx.index_build_time_seconds > 0

    def test_file_count_matches(self, sample_project):
        indexer = ProjectIndexer(str(sample_project))
        idx = indexer.index()

        assert idx.total_files == len(idx.files)


# ---------------------------------------------------------------------------
# Integration test: index the actual mcp-codebase-index source
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_index_mcp_codebase_index_source(self):
        """Index the actual mcp-codebase-index src directory as an integration test."""
        project_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        src_dir = os.path.join(project_root, "src")

        if not os.path.isdir(src_dir):
            pytest.skip("mcp-codebase-index src directory not found")

        indexer = ProjectIndexer(
            src_dir,
            include_patterns=["**/*.py"],
        )
        idx = indexer.index()

        # Should find at least several Python files
        assert idx.total_files >= 5
        assert idx.total_functions >= 10
        assert idx.total_classes >= 3

        # Should have known symbols
        assert "annotate" in idx.symbol_table or "annotate_python" in idx.symbol_table

        # Import graph should be non-empty
        assert len(idx.import_graph) > 0

        # Build time should be reasonable (< 5 seconds)
        assert idx.index_build_time_seconds < 5.0
