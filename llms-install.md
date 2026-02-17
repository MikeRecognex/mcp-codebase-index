# Installation

```bash
pip install "mcp-codebase-index[mcp]"
```

# Configuration

Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "codebase-index": {
      "command": "mcp-codebase-index",
      "env": { "PROJECT_ROOT": "/path/to/project" }
    }
  }
}
```

Replace `/path/to/project` with the actual project root directory.
