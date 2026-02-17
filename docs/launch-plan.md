# mcp-codebase-index: Launch & Go-to-Market Plan

**Date:** February 2026
**Version:** 0.2.0

---

## 1. Pricing Strategy

**mcp-codebase-index uses a dual-license model: AGPL-3.0 + Commercial.**

### How it works

- **AGPL-3.0 (free):** Anyone can use, modify, and distribute the software for free — as long as they comply with AGPL-3.0 terms (share source code of any modifications, including when offered as a network service).
- **Commercial License (paid):** Organizations that cannot or do not want to comply with AGPL-3.0 (e.g., embedding in proprietary products, offering as part of a SaaS platform, internal use without copyleft obligations) purchase a commercial license.

### Why this model

- **Maximum adoption.** Individual developers, open-source projects, and startups use it free. No friction.
- **IP protection.** AGPL copyleft prevents proprietary forks. Anyone who modifies and distributes must share their source.
- **Monetization path.** Companies that want to embed it in proprietary products must negotiate a commercial license with you.
- **Proven model.** Used successfully by MongoDB, Qt Company, MySQL AB, and many others.

### Who pays

The paying customers are **not** individual developers. They are:

| Customer type | Why they pay |
|---------------|-------------|
| AI IDEs (Cursor, Windsurf, etc.) | Embedding in a proprietary product — AGPL requires source disclosure |
| Enterprise internal tooling teams | Company policy prohibits AGPL dependencies |
| SaaS platforms | Offering as a hosted service triggers AGPL network-use clause |
| Companies wanting support/SLA | Commercial license includes priority support and indemnity |

### Future premium features (proprietary, not open-sourced)

| Feature | When | What |
|---------|------|------|
| Hosted/cloud version | When demand exists | Persistent index as a service, no local install needed |
| Real-time file watching | 3+ months | Incremental re-indexing on file save |
| Team shared indexes | 6+ months | Shared index across team members, RBAC |
| Enterprise support | Anytime | SLA, priority bug fixes, custom integrations |

**Bottom line:** Free for the community, paid for proprietary embedding. Commercial license details at [COMMERCIAL-LICENSE.md](../COMMERCIAL-LICENSE.md).

---

## 2. Token Savings Claims — Evidence

We measured actual tool responses against the RMLPlus codebase (15 files, 1,712 lines, 59,063 characters) to produce defensible numbers.

### Per-Query Savings

| Scenario | What the AI needs | Traditional approach | Indexed approach | Reduction |
|----------|-------------------|--------------------:|------------------:|----------:|
| List all functions | `get_functions()` | 59,063 chars (read all files) | 13,408 chars | **77%** |
| Trace dependencies | `get_dependencies("RLMEngine.run")` | 9,592 chars (read source file) | 121 chars | **99%** |
| Find a definition | `find_symbol("calculate_cost")` | ~400 chars (grep output) | 71 chars | **82%** |
| Read one function | `get_function_source("parse_response")` | 7,216 chars (read whole file) | 3,015 chars | **58%** |
| Impact analysis | `get_change_impact("LLMClient")` | 21,561 chars (read all impacted files) | 204 chars | **99%** |

**Weighted average: 87% reduction per query.**

The most conservative scenario (reading a single function) still saves 58%. Dependency and impact queries save 99% because they return structured data that traditional tools literally cannot produce without reading and analyzing multiple files.

### Multi-Turn Compounding

In messaging-based agents like OpenClaw, every tool response persists in the conversation for all subsequent turns. Over a 10-turn conversation with 3 codebase queries:

| Metric | Traditional | Indexed | Savings |
|--------|------------|---------|---------|
| Cumulative tokens consumed | ~205,000 | ~2,000 | **99%** |
| Peak context at turn 10 | ~32,000 tokens | ~900 tokens | **97%** |

The peak context number is what matters most: at turn 10, the traditional approach has ~32K tokens of stale file content consuming the context window, while indexed responses use only ~900 tokens — freeing **31,000 tokens** for actual reasoning.

### Defensible Marketing Claims

| Claim | Basis | Use where |
|-------|-------|-----------|
| "Reduces codebase query costs by 58-99% per query" | Direct measurement, conservative end | Technical docs, README |
| "87% average token reduction across common query patterns" | Weighted average of 5 measured scenarios | Blog posts, talks |
| "97%+ savings in multi-turn conversations" | Compounding measurement over 10 turns | OpenClaw-focused messaging |
| "Frees up to 31,000 tokens of context window" | Peak context comparison at turn 10 | Headlines, social media |

**Recommended headline claim:** "Reduces AI codebase navigation costs by up to 99%" — this is the measured maximum (dependency/impact queries), qualified with "up to."

**Do NOT claim:** "Cuts token costs by 60%" — this actually *undersells* the tool. The real numbers are much better.

---

## 3. Distribution Channels

### 3.1 Official MCP Registry (Top Priority)

The authoritative registry at https://registry.modelcontextprotocol.io/. This is where MCP clients discover servers.

**Steps:**
```bash
brew install mcp-publisher
mcp-publisher init                # generates server.json from pyproject.toml
mcp-publisher login github        # OAuth for io.github.mikerecognex/* namespace
mcp-publisher publish
```

**server.json** should include:
- Name: `io.github.mikerecognex/mcp-codebase-index`
- Description emphasizing 17 tools, zero dependencies, token savings
- PyPI package reference
- Transport: stdio
- Environment variable: `PROJECT_ROOT`

### 3.2 Community Directories (Submit to All)

| Directory | URL | Method |
|-----------|-----|--------|
| awesome-mcp-servers (78k stars) | https://github.com/punkpeye/awesome-mcp-servers | Pull request — add to "Code Analysis" category |
| mcpservers.org | https://mcpservers.org/submit | Web form |
| Smithery.ai | https://smithery.ai/ | `npx @anthropic-ai/smithery mcp publish` |
| Glama.ai | https://glama.ai/mcp/servers | GitHub OAuth |
| mcp.so | https://mcp.so | "Submit" nav link |
| PulseMCP | https://www.pulsemcp.com/servers | Web submission |
| Cline Marketplace | https://github.com/cline/mcp-marketplace | GitHub issue + 400x400 logo |
| MCP Server Finder | https://www.mcpserverfinder.com/ | "Submit Your Server" |

### 3.3 GitHub Discoverability

**Repository topics** (add via Settings > Topics):
```
mcp  model-context-protocol  mcp-server  codebase-indexer  code-analysis
python  typescript  dependency-graph  claude-code  ai-coding
ast-parser  symbol-table  code-navigation
```

**README badges** (add at the top):
```markdown
[![PyPI version](https://img.shields.io/pypi/v/mcp-codebase-index)](https://pypi.org/project/mcp-codebase-index/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)]()
```

Also generate one-click install badges at https://mcpbadge.dev/ for VS Code, Cursor, etc.

**CI workflow** — add `.github/workflows/ci.yml`:
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev,mcp]"
      - run: pytest tests/ -v
```

**Repository description** (one line, 160 chars):
> 17 MCP query tools for codebase navigation — functions, classes, imports, dependency graphs, change impact. Zero dependencies. 87% token reduction.

### 3.4 Create llms-install.md

Required for Cline Marketplace and helpful for any AI agent that needs to self-install. Place in repo root:

```markdown
# Installation

pip install "mcp-codebase-index[mcp]"

# Configuration

Add to .mcp.json:
{
  "mcpServers": {
    "codebase-index": {
      "command": "mcp-codebase-index",
      "env": { "PROJECT_ROOT": "/path/to/project" }
    }
  }
}

Replace /path/to/project with the actual project root directory.
```

---

## 4. Community & Social

### 4.1 Discord

**Official MCP Discord** (11,000+ members): https://discord.com/invite/model-context-protocol-1312302100125843476

Post in `#showcase` channel with:
- One-line description
- Screenshot or code block showing a query and response
- Link to GitHub and PyPI

### 4.2 Reddit

| Subreddit | Angle | Title idea |
|-----------|-------|------------|
| r/ClaudeAI | Token savings for Claude Code users | "I built an MCP server that reduces Claude Code's codebase reading by 87%" |
| r/Python | AST parsing, zero-dependency design | "Structural codebase indexer using only stdlib ast — no dependencies" |
| r/LocalLLaMA | Works with any MCP-compatible agent | "MCP codebase indexer: 17 query tools so your AI reads structure, not files" |
| r/programming | Technical Show-and-Tell | "Show r/programming: Structural codebase indexer for AI coding assistants" |

### 4.3 Hacker News

**Title:** `Show HN: MCP Codebase Index – 17 query tools so AI reads structure, not files`

**First comment:**
> I built this because AI coding assistants burn massive amounts of context window reading entire files to answer questions like "what does function X call?" or "what breaks if I change class Y?"
>
> mcp-codebase-index parses your codebase into a structural index — functions, classes, imports, dependency graphs — and exposes 17 query tools via MCP. Measured token savings: 58-99% per query depending on the operation, with 97%+ cumulative savings in multi-turn conversations.
>
> Design decisions: zero runtime dependencies (stdlib ast + regex), in-memory indexing (~1-2s rebuild), output size controls for token budget management. Supports Python (AST), TypeScript/JS (regex), Markdown (headings).
>
> pip install "mcp-codebase-index[mcp]"
>
> GitHub: https://github.com/MikeRecognex/mcp-codebase-index

**Timing:** Tuesday-Thursday, 8-10am ET.

### 4.4 Twitter/X

**Thread format (5 tweets):**

1. "I measured how many tokens AI coding assistants waste reading entire files to answer structural questions. The answer: 87% of what they read is wasted. So I built something about it. [thread]"

2. "mcp-codebase-index parses your codebase into functions, classes, imports, and dependency graphs, then exposes 17 surgical MCP query tools. Instead of reading a 500-line file, the AI gets a 5-line answer."

3. "Measured results against a real codebase: find_symbol: 82% reduction. get_dependencies: 99% reduction. get_change_impact: 99% reduction. Even the worst case (reading one function): 58% reduction."

4. "In multi-turn conversations (OpenClaw, etc.) the savings compound. By turn 10, traditional file reading has 32K tokens of stale content in context. Indexed approach: 900 tokens. That's 31K tokens freed for actual reasoning."

5. "Zero dependencies (stdlib ast + regex). In-memory indexing. Python + TypeScript/JS + Markdown. Free and open source. pip install mcp-codebase-index. GitHub: [link] #MCP #ClaudeCode"

**Accounts to tag/engage:** @AnthropicAI, @alexalbert__, @punkpeye

---

## 5. Content Marketing

### 5.1 Blog Posts

| Post | Platform | Audience | Timing |
|------|----------|----------|--------|
| "How Structural Indexing Cuts AI Token Costs by 87%" | dev.to | AI-assisted dev practitioners | Week 1 |
| "Zero-Dependency MCP Server Design: Why stdlib Is Enough" | dev.to | Minimalist engineers, HN readers | Week 2 |
| "17 MCP Tools for Codebase Navigation: A Practical Guide" | dev.to | MCP server users looking for tools | Week 3 |
| "Building Cross-File Dependency Graphs with Python's AST" | dev.to / Medium | Python developers | Week 4 |

**Tags for dev.to:** `mcp`, `python`, `ai`, `claudecode`, `openai`, `tooling`

### 5.2 Demo Video (2-3 minutes)

Script:
1. (0:00-0:30) Install and configure — `pip install`, add to `.mcp.json`
2. (0:30-1:00) `get_project_summary` on a real project — show the overview
3. (1:00-1:30) `find_symbol` + `get_function_source` — surgical code access
4. (1:30-2:00) `get_change_impact` — "what breaks if I change this?"
5. (2:00-2:30) Side-by-side: context window usage with vs. without indexer

Host on YouTube, embed in README. Share link in all community posts.

---

## 6. IDE & Tool Integrations

Test and document configuration for each. Add a section to README for each.

| Tool | MCP support | Priority | Notes |
|------|-------------|----------|-------|
| Claude Code | Yes (native) | Already done | Already documented in README |
| OpenClaw | Yes (via mcporter) | Already done | Already documented in README |
| Cursor | Yes (native) | High | Large user base, test and add docs |
| VS Code + Copilot | Yes (native, recent) | High | Generate install badge via mcpbadge.dev |
| Windsurf (Codeium) | Yes | Medium | Growing user base |
| Continue.dev | Yes | Medium | Open source, good partnership fit |

---

## 7. Upcoming Events & Timing

| Date | Event | Action |
|------|-------|--------|
| **Feb 24, 2026** | Anthropic "The Briefing: Enterprise Agents" livestream | Watch. Post about integration if they announce MCP features. |
| **Apr 2-3, 2026** | MCP Dev Summit (NYC, Linux Foundation) | Attend. Network. Demo in hallway track. Register at https://events.linuxfoundation.org/mcp-dev-summit-north-america/register/ |
| **Jun 2026** | MCP statelessness spec expected (AAIF) | Adapt server to new specs early — first-mover advantage. |

---

## 8. Execution Calendar

### Week 1 (Feb 17-21)

| Day | Task | Time |
|-----|------|------|
| Mon | Add GitHub topics (13 tags). Add README badges. Create CI workflow. | 1 hr |
| Mon | Create `server.json`, publish to official MCP Registry. | 30 min |
| Mon | Create `llms-install.md` for Cline. | 15 min |
| Tue | Submit to all 8 directories (see 3.2). | 1 hr |
| Tue | Open PR on punkpeye/awesome-mcp-servers. | 20 min |
| Tue | Create 400x400 logo. Submit to Cline Marketplace. | 30 min |
| Wed | Post Show HN (8-10am ET). | 30 min |
| Wed | Post in MCP Discord #showcase. | 15 min |
| Wed | Post to r/ClaudeAI and r/Python. | 30 min |
| Thu | Record and upload demo video. Embed in README. | 2 hr |
| Thu | Post Twitter/X thread. | 30 min |
| Fri | Publish "How Structural Indexing Cuts AI Token Costs by 87%" on dev.to. | 2 hr |

### Week 2 (Feb 24-28)

| Day | Task |
|-----|------|
| Mon | Watch Anthropic "The Briefing" livestream. React-post if relevant. |
| Tue | Test and document Cursor configuration. |
| Wed | Test and document VS Code + Copilot configuration. Generate mcpbadge.dev badges. |
| Thu | Publish "Zero-Dependency MCP Server Design" blog post. |
| Fri | Post to r/programming and r/LocalLLaMA. |

### Week 3-4

| Task |
|------|
| Publish remaining blog posts (tools field guide, AST tutorial). |
| Test Windsurf and Continue.dev integrations. |
| Monitor and respond to community feedback. |
| Iterate on features based on user requests. |

---

## 9. Success Metrics

Track these to know if the launch is working:

| Metric | Target (30 days) | How to measure |
|--------|-------------------|----------------|
| GitHub stars | 100+ | GitHub insights |
| PyPI downloads | 500+ | https://pypistats.org/packages/mcp-codebase-index |
| MCP Registry installs | Track if available | Registry dashboard |
| Hacker News points | 50+ | HN front page = major win |
| Community directory listings | 8+ confirmed | Check each directory |
| Blog post views | 1,000+ total | dev.to analytics |
| GitHub issues/PRs from users | 5+ | Signal of real usage |

---

## 10. Summary

**Pricing:** Free and open source (MIT). No reason to charge now. Monetize later with hosted/enterprise if demand warrants.

**Core claim:** "Reduces AI codebase navigation costs by 87% on average, 97%+ in multi-turn conversations." Backed by measured data against a real codebase.

**Distribution strategy:** Official MCP Registry + 8 community directories + Hacker News + Reddit + Twitter + Discord + dev.to blog posts + demo video.

**Timeline:** Full launch in one week. Content marketing over weeks 2-4. MCP Dev Summit in April for in-person networking.
