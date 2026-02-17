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

"""Dispatch layer that selects the appropriate annotator by file type."""

from mcp_codebase_index.generic_annotator import annotate_generic
from mcp_codebase_index.models import StructuralMetadata
from mcp_codebase_index.python_annotator import annotate_python
from mcp_codebase_index.text_annotator import annotate_text
from mcp_codebase_index.typescript_annotator import annotate_typescript

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".md": "text",
    ".txt": "text",
    ".rst": "text",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}


def annotate(
    text: str,
    source_name: str = "<source>",
    file_type: str | None = None,
) -> StructuralMetadata:
    """Annotate text with structural metadata.

    Dispatch rules:
    - file_type overrides extension-based detection
    - .py -> python annotator
    - .md, .txt, .rst -> text annotator
    - .ts, .tsx -> typescript annotator
    - .js, .jsx -> typescript annotator (close enough for regex-based parsing)
    - Otherwise -> generic annotator (line-only)
    """
    if file_type is None:
        # Detect from source_name extension
        dot_idx = source_name.rfind(".")
        if dot_idx >= 0:
            ext = source_name[dot_idx:].lower()
            file_type = _EXTENSION_MAP.get(ext)

    if file_type == "python":
        return annotate_python(text, source_name)
    elif file_type == "text":
        return annotate_text(text, source_name)
    elif file_type in ("typescript", "javascript"):
        return annotate_typescript(text, source_name)
    else:
        return annotate_generic(text, source_name)
