# Vasily AI

AI agent with modular ReAct architecture — art generation, web search, and more.

**Status:** 🚧 Active development · Core complete · Memory integration in progress

---

## Overview

Vasily AI is an asynchronous AI agent that combines **reasoning + acting** (ReAct pattern) with a plugin-based tool system. It connects to a local LLM (Ollama) and can orchestrate multi-step tasks — searching the web, scraping pages, generating art prompts, querying Danbooru, and more.

Designed for **production stability** with structured logging, crash reporting, input sanitization, and robust error handling.

---

## Features

| Area | Capability |
|------|------------|
| **Core** | ReAct loop with tool calling · Async asyncio core · Plugin auto-discovery |
| **Plugins** | Art prompt generation · Web search (SearXNG) · Web scraping (SSRF-protected) · Danbooru search · Echo (testing) |
| **Memory** | Hot/Cold two-tier storage — *currently being integrated* · LLM-powered compression (planned) |
| **Logging** | 4 categories (core/interaction/plugins/llm) · 5 alert levels · 24h rotation · 3-day retention |
| **Safety** | Crash reports on fatal errors · Log sanitization (PII masking) · SSRF protection · Mock data only in dev mode |
| **LLM** | Ollama client with retries · Configurable context window (num_ctx) · Health checks |

---

## Quick Start

### Requirements
- Python 3.10+
- [Ollama](https://ollama.ai/) with a compatible model (recommended: `vasily-qwen` or `llama3.2`)
- [SearXNG](https://docs.searxng.org/) (optional, for web search)

### Installation

```bash
uv sync --all-extras

##Configuration
Create vasily_config.json in the project root:
{
  "llm_url": "http://localhost:11434",
  "llm_model": "vasily-qwen",
  "searxng_url": "http://localhost:8080/search",
  "dev_mode": false,
  "max_react_iterations": 6
}

Alternatively, use environment variables:
export VASILY_LLM_MODEL=llama3.2
export VASILY_DEV_MODE=true

#Run
python -m vasily_ai

Interactive CLI:
> Нарисуй самурая под дождём
> Поищи информацию про Stable Diffusion
> status
> help
> exit

##Plugins
Plugin	Description
art_generator	Creates detailed prompts for Stable Diffusion / Midjourney
web_search	Searches the web via SearXNG
web_scraper	Extracts page content with SSRF protection (blocks local/private IPs)
danbooru_search	Searches Danbooru posts and tags
echo	Test plugin — returns input as-is
Plugins are auto-discovered. Just drop a new plugin into plugins/ and it will be available to the agent.


##Project Structure
vasily_ai/
├── core/               # Core components
│   ├── agent.py        # AgentCore — orchestration
│   ├── react_loop.py   # ReAct reasoning + acting
│   ├── config.py       # Configuration (env + file + defaults)
│   ├── plugin_registry.py
│   ├── token_manager.py
│   ├── golden_prompts.py
│   ├── logging_config.py
│   ├── crash_reporter.py
│   └── health_check.py
├── plugins/            # Auto-discovered plugins
│   ├── art_generator/
│   ├── web_search/
│   ├── web_scraper/
│   ├── danbooru/
│   └── echo/
├── memory/             # Memory subsystem (integration in progress)
│   ├── manager.py
│   ├── long_term.py
│   └── llm_compressor.py
├── integrations/       # External services
│   └── ollama_client.py
├── tests/              # Test suite
├── logs/               # Rotated logs (auto-created)
└── data/               # Persistent data (auto-created)

##Testing
Run the full test suite:
pytest tests/ -v

Key test scenarios:
python test_intelligence.py   # P2-2 metrics: scenario pass rate, tool accuracy, graceful error handling
python test_react_loop.py     # ReAct loop with real LLM and plugins
python test_ollama_client.py  # LLM client with retries and crash reports

##Logging & Monitoring
Logs are written to logs/ with 24‑hour rotation and 3‑day retention:

File	Category
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Core ↔ Plugin calls
plugins.log	Plugin internals
llm.log	LLM requests/responses
vasily.log	All logs combined
Alert levels (auto-detected):

STATE — System state

REQUEST — User requests

WARNING — Warnings

CRITICAL_WARNING — Critical issues

CRASH — Fatal crashes

Crash reports are saved to logs/crash_reports/ in both JSON and Markdown.


##Roadmap
Status	Feature
✅	ReAct loop with tool calling
✅	Plugin system with auto-discovery
✅	Structured logging with rotation
✅	Crash reporting
✅	SSRF protection
✅	Log sanitization
🔄	Two-tier memory (Hot/Cold) — integration in progress
🔄	LLM-powered memory compression
📋	MCP (Model Context Protocol) support
📋	Web UI / API server
##License
MIT
