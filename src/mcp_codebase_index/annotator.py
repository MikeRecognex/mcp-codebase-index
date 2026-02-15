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
