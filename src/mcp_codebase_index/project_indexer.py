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

"""Project-wide codebase indexer.

Walks a project directory, annotates each file using the dispatch annotator,
builds cross-file dependency graphs, import graphs, and a global symbol table.
"""

import fnmatch
import logging
import os
import re
import sys
import time
from pathlib import Path

from mcp_codebase_index.annotator import annotate
from mcp_codebase_index.models import ProjectIndex, StructuralMetadata

logger = logging.getLogger(__name__)


class ProjectIndexer:
    """Indexes an entire codebase for structural navigation."""

    def __init__(
        self,
        root_path: str,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_file_size_bytes: int = 500_000,
    ):
        self.root_path = os.path.abspath(root_path)
        self.include_patterns = include_patterns or [
            "**/*.py",
            "**/*.ts",
            "**/*.tsx",
            "**/*.js",
            "**/*.jsx",
            "**/*.md",
            "**/*.txt",
        ]
        self.exclude_patterns = exclude_patterns or [
            "**/__pycache__/**",
            "**/node_modules/**",
            "**/.git/**",
            "**/.venv/**",
            "**/venv/**",
            "**/*.egg-info/**",
        ]
        self.max_file_size_bytes = max_file_size_bytes
        self._project_index: ProjectIndex | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index(self) -> ProjectIndex:
        """Walk the project, annotate all files, build cross-file graphs.

        Steps:
        1. Discover files using pathlib.Path.glob matching include patterns,
           filtering out exclude patterns
        2. Read and annotate each file using the dispatch annotator
        3. Build global symbol table: for each file's functions and classes,
           map qualified_name -> file_path
        4. Build cross-file import graph: for each file's imports, resolve to
           actual project files using Python module resolution
        5. Build reverse import graph
        6. Build global dependency graph: merge per-file dependency graphs,
           resolve cross-file references via symbol table
        7. Build reverse dependency graph
        8. Record timing and stats

        Returns:
            ProjectIndex with all files indexed and cross-references built.
        """
        start_time = time.monotonic()

        # Step 1: discover files
        file_paths = self._discover_files()
        logger.info("Discovered %d files in %s", len(file_paths), self.root_path)

        # Step 2: annotate each file
        files: dict[str, StructuralMetadata] = {}
        total_lines = 0
        total_functions = 0
        total_classes = 0

        for fpath in file_paths:
            rel_path = os.path.relpath(fpath, self.root_path)
            try:
                source = self._read_file(fpath)
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Skipping %s: %s", rel_path, e)
                continue

            metadata = annotate(source, source_name=rel_path)
            files[rel_path] = metadata
            total_lines += metadata.total_lines
            total_functions += len(metadata.functions)
            total_classes += len(metadata.classes)

        # Step 3: build global symbol table
        symbol_table = self._build_symbol_table(files)

        # Step 4: build cross-file import graph
        import_graph = self._build_import_graph(files)

        # Step 5: build reverse import graph
        reverse_import_graph = self._build_reverse_graph(import_graph)

        # Step 6: build global dependency graph
        global_dep_graph = self._build_global_dependency_graph(files, symbol_table)

        # Step 7: build reverse dependency graph
        reverse_dep_graph = self._build_reverse_graph(global_dep_graph)

        elapsed = time.monotonic() - start_time

        self._project_index = ProjectIndex(
            root_path=self.root_path,
            files=files,
            global_dependency_graph=global_dep_graph,
            reverse_dependency_graph=reverse_dep_graph,
            import_graph=import_graph,
            reverse_import_graph=reverse_import_graph,
            symbol_table=symbol_table,
            total_files=len(files),
            total_lines=total_lines,
            total_functions=total_functions,
            total_classes=total_classes,
            index_build_time_seconds=elapsed,
            index_memory_bytes=sys.getsizeof(files),  # rough estimate
        )

        logger.info(
            "Indexed %d files (%d lines, %d functions, %d classes) in %.2fs",
            len(files),
            total_lines,
            total_functions,
            total_classes,
            elapsed,
        )

        return self._project_index

    def reindex_file(self, file_path: str) -> None:
        """Re-index a single file. Updates the existing ProjectIndex in place.

        Args:
            file_path: Path to the file (absolute or relative to root_path).
        """
        if self._project_index is None:
            raise RuntimeError("Cannot reindex before initial index() call.")

        # Normalize to relative path
        abs_path = (
            os.path.abspath(file_path)
            if os.path.isabs(file_path)
            else os.path.join(self.root_path, file_path)
        )
        rel_path = os.path.relpath(abs_path, self.root_path)

        idx = self._project_index

        # Remove old data for this file
        old_metadata = idx.files.get(rel_path)
        if old_metadata is not None:
            # Remove old symbols from symbol table
            for func in old_metadata.functions:
                if idx.symbol_table.get(func.qualified_name) == rel_path:
                    del idx.symbol_table[func.qualified_name]
                if idx.symbol_table.get(func.name) == rel_path:
                    del idx.symbol_table[func.name]
            for cls in old_metadata.classes:
                if idx.symbol_table.get(cls.name) == rel_path:
                    del idx.symbol_table[cls.name]

            # Remove old entries from import graphs
            idx.import_graph.pop(rel_path, None)
            for targets in idx.reverse_import_graph.values():
                targets.discard(rel_path)
            # Clean up reverse import graph entries pointing from this file
            for target_file in list(idx.reverse_import_graph.keys()):
                idx.reverse_import_graph[target_file].discard(rel_path)

            # Update stats
            idx.total_lines -= old_metadata.total_lines
            idx.total_functions -= len(old_metadata.functions)
            idx.total_classes -= len(old_metadata.classes)

        # Read and annotate the updated file
        try:
            source = self._read_file(abs_path)
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Cannot reindex %s: %s", rel_path, e)
            if rel_path in idx.files:
                del idx.files[rel_path]
                idx.total_files = len(idx.files)
            return

        metadata = annotate(source, source_name=rel_path)
        idx.files[rel_path] = metadata
        idx.total_files = len(idx.files)
        idx.total_lines += metadata.total_lines
        idx.total_functions += len(metadata.functions)
        idx.total_classes += len(metadata.classes)

        # Rebuild symbol table entries for this file
        for func in metadata.functions:
            if func.qualified_name not in idx.symbol_table:
                idx.symbol_table[func.qualified_name] = rel_path
            if func.name not in idx.symbol_table:
                idx.symbol_table[func.name] = rel_path
        for cls in metadata.classes:
            if cls.name not in idx.symbol_table:
                idx.symbol_table[cls.name] = rel_path

        # Rebuild import graph for this file
        file_imports = self._resolve_imports_for_file(rel_path, metadata, idx.files)
        if file_imports:
            idx.import_graph[rel_path] = file_imports
        else:
            idx.import_graph.pop(rel_path, None)

        # Rebuild reverse import graph
        idx.reverse_import_graph = self._build_reverse_graph(idx.import_graph)

        # Rebuild global dependency graphs (full rebuild is simplest for correctness)
        idx.global_dependency_graph = self._build_global_dependency_graph(
            idx.files, idx.symbol_table
        )
        idx.reverse_dependency_graph = self._build_reverse_graph(
            idx.global_dependency_graph
        )

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _discover_files(self) -> list[str]:
        """Discover files matching include patterns, excluding exclude patterns."""
        root = Path(self.root_path)
        matched: set[str] = set()

        for pattern in self.include_patterns:
            for p in root.glob(pattern):
                if p.is_file():
                    abs_str = str(p)
                    rel_str = os.path.relpath(abs_str, self.root_path)

                    if self._is_excluded(rel_str):
                        continue

                    # Check file size
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    if size > self.max_file_size_bytes:
                        logger.debug("Skipping %s (size %d > %d)", rel_str, size, self.max_file_size_bytes)
                        continue

                    matched.add(abs_str)

        return sorted(matched)

    def _is_excluded(self, rel_path: str) -> bool:
        """Check if a relative path matches any exclude pattern."""
        # Normalize separators to forward slashes for matching
        normalized = rel_path.replace(os.sep, "/")
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(normalized, pattern):
                return True
            # Also check if any path component matches
            # e.g., "__pycache__" in the path
            parts = normalized.split("/")
            # Check simple directory name exclusions
            pattern_parts = pattern.replace("**/", "").replace("/**", "").strip("/")
            if pattern_parts in parts:
                return True
        return False

    def _read_file(self, abs_path: str) -> str:
        """Read a file as text, trying UTF-8 first then latin-1 as fallback."""
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(abs_path, "r", encoding="latin-1") as f:
                return f.read()

    # ------------------------------------------------------------------
    # Symbol table
    # ------------------------------------------------------------------

    def _build_symbol_table(
        self, files: dict[str, StructuralMetadata]
    ) -> dict[str, str]:
        """Build global symbol table: symbol_name -> file_path where defined.

        For methods, use qualified_name (e.g., "MyClass.run" -> "src/engine.py").
        First-found wins for duplicates.
        """
        symbol_table: dict[str, str] = {}

        for file_path, metadata in files.items():
            for func in metadata.functions:
                # Register by qualified name (e.g., "MyClass.method")
                if func.qualified_name not in symbol_table:
                    symbol_table[func.qualified_name] = file_path
                # Also register by simple name for top-level functions
                if not func.is_method and func.name not in symbol_table:
                    symbol_table[func.name] = file_path

            for cls in metadata.classes:
                if cls.name not in symbol_table:
                    symbol_table[cls.name] = file_path

        return symbol_table

    # ------------------------------------------------------------------
    # Import graph
    # ------------------------------------------------------------------

    def _build_import_graph(
        self, files: dict[str, StructuralMetadata]
    ) -> dict[str, set[str]]:
        """Build file-level import graph: file -> set of files it imports from."""
        import_graph: dict[str, set[str]] = {}

        for file_path, metadata in files.items():
            targets = self._resolve_imports_for_file(file_path, metadata, files)
            if targets:
                import_graph[file_path] = targets

        return import_graph

    def _resolve_imports_for_file(
        self,
        file_path: str,
        metadata: StructuralMetadata,
        all_files: dict[str, StructuralMetadata],
    ) -> set[str]:
        """Resolve a file's imports to other project files."""
        targets: set[str] = set()
        all_file_set = set(all_files.keys())

        for imp in metadata.imports:
            resolved = self._resolve_import(file_path, imp.module, imp.is_from_import, all_file_set)
            if resolved and resolved != file_path:
                targets.add(resolved)

        return targets

    def _resolve_import(
        self,
        importing_file: str,
        module_path: str,
        is_from_import: bool,
        all_files: set[str],
    ) -> str | None:
        """Resolve an import module path to a project file path.

        For Python files:
        - Convert module path to file path (dots to slashes)
        - Look for module.py or module/__init__.py
        - Search relative to root and common source dirs (src/, lib/)

        For TypeScript/JavaScript files:
        - Resolve relative paths (./foo, ../bar)
        - Try common path aliases (@/ -> src/)
        """
        if not module_path:
            return None

        ext = os.path.splitext(importing_file)[1].lower()

        if ext == ".py":
            return self._resolve_python_import(module_path, all_files)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            return self._resolve_ts_import(importing_file, module_path, all_files)

        return None

    def _resolve_python_import(
        self, module_path: str, all_files: set[str]
    ) -> str | None:
        """Resolve a Python module path to a project file."""
        # Convert dots to path separators
        rel_module = module_path.replace(".", "/")

        # Search directories: root, src/, lib/
        search_prefixes = ["", "src/", "lib/"]

        for prefix in search_prefixes:
            # Try as a .py file
            candidate = prefix + rel_module + ".py"
            candidate_normalized = candidate.replace(os.sep, "/")
            if candidate_normalized in all_files:
                return candidate_normalized

            # Try as a package (__init__.py)
            candidate = prefix + rel_module + "/__init__.py"
            candidate_normalized = candidate.replace(os.sep, "/")
            if candidate_normalized in all_files:
                return candidate_normalized

        return None

    def _resolve_ts_import(
        self, importing_file: str, module_path: str, all_files: set[str]
    ) -> str | None:
        """Resolve a TypeScript/JavaScript import path to a project file.

        Handles:
        - Relative paths: './utils' -> try utils.ts, utils.tsx, utils/index.ts, etc.
        - Path aliases: '@/lib/utils' -> try src/lib/utils.ts, etc.
        """
        extensions = [".ts", ".tsx", ".js", ".jsx"]

        if module_path.startswith("."):
            # Relative import
            importing_dir = os.path.dirname(importing_file)
            base = os.path.normpath(os.path.join(importing_dir, module_path))
            base = base.replace(os.sep, "/")
        elif module_path.startswith("@/"):
            # Common path alias: @/ -> src/
            base = "src/" + module_path[2:]
        else:
            # Likely an external package (e.g., 'react', 'lodash')
            return None

        # Try exact match first (might already have extension)
        if base in all_files:
            return base

        # Try with extensions
        for ext in extensions:
            candidate = base + ext
            if candidate in all_files:
                return candidate

        # Try as directory with index file
        for ext in extensions:
            candidate = base + "/index" + ext
            if candidate in all_files:
                return candidate

        return None

    # ------------------------------------------------------------------
    # Dependency graph
    # ------------------------------------------------------------------

    def _build_global_dependency_graph(
        self,
        files: dict[str, StructuralMetadata],
        symbol_table: dict[str, str],
    ) -> dict[str, set[str]]:
        """Build global dependency graph: qualified_name -> set of qualified_names.

        Merges per-file dependency graphs and resolves cross-file references
        via the symbol table and import information.

        The per-file dependency graph only tracks references to names defined
        in the same file. For cross-file dependencies, we also check each
        function/class body for references to imported names.
        """
        global_graph: dict[str, set[str]] = {}

        # Set of all known qualified names in the project
        all_symbols = set(symbol_table.keys())

        for file_path, metadata in files.items():
            # Collect imported names mapping: local_name -> qualified_name (symbol table key)
            imported_names: dict[str, str] = {}
            for imp in metadata.imports:
                for name in imp.names:
                    # Check if this name is a known symbol
                    if name in symbol_table:
                        imported_names[name] = name

            # Process per-file dependency graph (intra-file deps)
            for source_name, deps in metadata.dependency_graph.items():
                source_qualified = self._qualify_name(source_name, file_path, symbol_table)
                if source_qualified not in global_graph:
                    global_graph[source_qualified] = set()

                for dep in deps:
                    dep_qualified = None

                    # Check if it's an imported name
                    if dep in imported_names:
                        dep_qualified = imported_names[dep]

                    # Check if it's a local name in the same file
                    if dep_qualified is None:
                        candidate = self._qualify_name(dep, file_path, symbol_table)
                        if candidate in all_symbols:
                            dep_qualified = candidate

                    # Check if it's a known global symbol
                    if dep_qualified is None and dep in all_symbols:
                        dep_qualified = dep

                    if dep_qualified and dep_qualified != source_qualified:
                        global_graph[source_qualified].add(dep_qualified)

            # Now handle cross-file dependencies by scanning function/class bodies
            # for references to imported names (which the per-file dep graph misses).
            if not imported_names:
                continue

            for func in metadata.functions:
                func_qualified = self._qualify_name(
                    func.qualified_name, file_path, symbol_table
                )
                if func_qualified not in global_graph:
                    global_graph[func_qualified] = set()

                # Scan the function body lines for imported name references
                start_idx = func.line_range.start - 1  # 0-indexed
                end_idx = func.line_range.end  # exclusive
                body_text = " ".join(metadata.lines[start_idx:end_idx])
                for local_name, resolved_name in imported_names.items():
                    if re.search(r'\b' + re.escape(local_name) + r'\b', body_text):
                        if resolved_name != func_qualified:
                            global_graph[func_qualified].add(resolved_name)

            for cls in metadata.classes:
                cls_qualified = self._qualify_name(cls.name, file_path, symbol_table)
                if cls_qualified not in global_graph:
                    global_graph[cls_qualified] = set()

                # Scan the class body lines for imported name references
                start_idx = cls.line_range.start - 1
                end_idx = cls.line_range.end
                body_text = " ".join(metadata.lines[start_idx:end_idx])
                for local_name, resolved_name in imported_names.items():
                    if re.search(r'\b' + re.escape(local_name) + r'\b', body_text):
                        if resolved_name != cls_qualified:
                            global_graph[cls_qualified].add(resolved_name)

        return global_graph

    def _qualify_name(
        self, name: str, file_path: str, symbol_table: dict[str, str]
    ) -> str:
        """Given a local name and file path, find the qualified name in the symbol table."""
        # If the name is already a qualified name in the symbol table, use it
        if name in symbol_table and symbol_table[name] == file_path:
            return name

        # Check if it's a method-style qualified name (Class.method)
        # The symbol table should already have these
        return name

    # ------------------------------------------------------------------
    # Graph utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _build_reverse_graph(
        graph: dict[str, set[str]],
    ) -> dict[str, set[str]]:
        """Build a reverse graph: for each target, collect all sources."""
        reverse: dict[str, set[str]] = {}
        for source, targets in graph.items():
            for target in targets:
                if target not in reverse:
                    reverse[target] = set()
                reverse[target].add(source)
        return reverse
