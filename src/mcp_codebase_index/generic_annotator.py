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

"""Generic fallback annotator providing line-only metadata."""

from mcp_codebase_index.models import StructuralMetadata


def annotate_generic(text: str, source_name: str = "<source>") -> StructuralMetadata:
    """Create minimal structural metadata with just line information."""
    lines = text.splitlines()
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line) + 1  # +1 for newline

    return StructuralMetadata(
        source_name=source_name,
        total_lines=len(lines),
        total_chars=len(text),
        lines=lines,
        line_char_offsets=offsets,
    )
