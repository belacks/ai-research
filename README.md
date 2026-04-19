# CRN — Crawl Research Node

*Lightweight autonomous research agent built from scratch on Ollama + Docker + Telegram*

![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-Local_LLM-000000?logo=ollama&logoColor=white)

## What Is This?

CRN is a self-hosted, hardware-local AI research agent. It crawls a configurable list of web sources on a schedule, summarizes each page through a local LLM running on Ollama, and delivers structured intelligence briefings directly to your Telegram. Everything runs on your own machine — no API keys to OpenAI, no cloud compute bills, no data leaving your network.

The entire system is built from scratch without AutoGen, CrewAI, LangChain, or any agentic framework. Every component is explicit Python: a Playwright crawler, a Trafilatura cleaner, a raw HTTP call to Ollama, a JSON parser, a SQLite writer, and a Telegram bot. This is intentional. On consumer hardware (laptops, mini PCs), resource control matters more than abstraction convenience.

## Architecture

```
targets.yaml → Playwright Crawler → Trafilatura Cleaner
                                          ↓
                              Ollama (host, any model)
                                          ↓
                           JSON Parser + Eligibility Gate
                                          ↓
                    SQLite DB ←→ Telegram Bot + Streamlit Dashboard
```

| Component | Role |
|-----------|------|
| Playwright | Headless Chromium browser for JavaScript-rendered page extraction |
| Trafilatura | DOM cleaning — strips navigation, sidebars, and boilerplate to isolate article text |
| Ollama | Local LLM inference server running on the host machine |
| python-telegram-bot | Bidirectional Telegram interface with inline keyboards and command routing |
| SQLite | Structured storage for every crawl result, enabling pattern queries and historical analysis |
| Streamlit | Dashboard UI for triggering crawls and viewing briefing history |
| Docker Compose | Two-container deployment with security hardening (non-root, read-only filesystem) |

## Features

CRN crawls multiple sources per cycle with scope-aware filtering — sources are tagged as `global` (international tech, AI research, strategy) or `local` (Indonesia, Southeast Asia) and users can run targeted briefings via Telegram commands like `/briefing local`. Each source is routed to a domain-specific LLM persona: a Senior Engineering Lead for tech sites, a Quantitative Analyst for finance sources, an AI Research Scientist for papers, and so on. The LLM is instructed to return a strict JSON schema containing a title, executive summary, up to three key insights, relevance tags from a fixed taxonomy, a relevance score calibrated against the user's personal researcher profile, and a concrete action item. That profile is defined in `.env` — a plain-text description of your background, thesis topic, and career interests — so relevance scoring is personalized, not generic.

Every crawl result is persisted to a SQLite database with full metadata: URL, category, scope, structured fields, raw character count, and whether the item passed the eligibility gate and was actually sent to Telegram. The eligibility gate is deterministic — it checks extraction quality and parse success, not subjective LLM scoring. Failed or low-quality extractions are stored but silently skipped for notification. The entire system runs inside Docker Compose with production security hardening: containers run as a non-root user, the filesystem is mounted read-only, all Linux capabilities are dropped, and privilege escalation is blocked.

## Quick Start

1. **Prerequisites**: Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [Ollama](https://ollama.com/). Create a Telegram bot via [@BotFather](https://t.me/BotFather) and note the token.

2. **Clone the repository**:
   ```bash
   git clone https://github.com/belacks/ai-research.git
   cd ai-research
   ```

3. **Pull a model** (any Ollama-compatible model works):
   ```bash
   ollama pull qwen3:2b
   ```

4. **Copy and fill the config**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your Telegram bot token, chat ID, and preferred model name.

5. **Add or remove sources**: Edit `targets.yaml` to configure which sites CRN crawls. No Python changes required.

6. **Run**:
   ```bash
   docker compose up --build -d
   ```
   Send `/menu` to your Telegram bot to verify it's online.

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Your Telegram user/group chat ID |
| `OLLAMA_BASE_URL` | Yes | Ollama API endpoint (default: `http://host.docker.internal:11434`) |
| `TARGET_MODEL` | Yes | Ollama model name (must be pulled locally, e.g. `qwen3:2b`) |
| `SCHEDULE_INTERVAL_HOURS` | Yes | Hours between scheduled crawl reminders |
| `RESEARCHER_PROFILE` | No | Your background description for personalized relevance scoring. Default: `"A technology researcher interested in AI, Data Science, and global tech trends."` |

### Source Configuration (targets.yaml)

Sources are defined in `targets.yaml` at the project root. Each entry has three fields:

```yaml
targets:
  - url: "https://huggingface.co/blog"
    category: "AI Research"
    scope: "global"
```

- `url` — the page to crawl
- `category` — free-form label displayed in Telegram notifications
- `scope` — either `"global"` or `"local"`. Used by `/briefing local` and `/briefing global` to filter sources

The file is mounted as a read-only volume inside the container. Changes take effect on the next crawl cycle without rebuilding the image.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/menu` | Open the interactive control panel with inline keyboard buttons |
| `/briefing local` | Run a crawl cycle on local (Indonesia/SEA) sources only |
| `/briefing global` | Run a crawl cycle on global (international) sources only |
| `/briefing all` | Run a full crawl cycle on all configured sources |

## Roadmap

- [x] Trafilatura DOM cleaning
- [x] Structured JSON output with researcher profile
- [x] SQLite intelligence database
- [x] Scope-filtered Telegram commands
- [ ] Async concurrent crawling (asyncio.gather)
- [ ] RAG / vector memory across past briefings
- [ ] Trading signal agent (MT5 integration)

## Why Not AutoGen / CrewAI?

Those frameworks are powerful but heavy — they abstract away the parts that matter most for learning and for resource-constrained hardware. CRN is intentionally minimal. There is no agent orchestration layer, no tool-calling abstraction, no prompt chaining middleware. Every component is a plain Python function with explicit inputs and outputs. If the crawler breaks, you read the crawler. If the LLM prompt needs tuning, you edit the prompt string. On a laptop running a 2B parameter model on CPU, that level of control is not optional — it is the architecture.

## License

[MIT](LICENSE)
