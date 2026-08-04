# 📘 Crawl Research Node (CRN) — Complete Technical Guidebook

Welcome to the comprehensive technical manual for **Crawl Research Node (CRN)**. This guidebook covers system architecture, modular design patterns, plugin creation, custom bank adapters, multi-tier RAG search, and operations.

---

## 📑 Table of Contents
1. [Architecture Philosophy & Design Patterns](#1-architecture-philosophy--design-patterns)
2. [Dual-Brain LLM Cascade Engine](#2-dual-brain-llm-cascade-engine)
3. [Universal 2-Tier Intent Router & Rate Limiter](#3-universal-2-tier-intent-router--rate-limiter)
4. [4-Tier RAG & Hybrid Vector Memory](#4-4-tier-rag--hybrid-vector-memory)
5. [Autonomous Job Intelligence Pipeline](#5-autonomous-job-intelligence-pipeline)
6. [Pluggable Finance Engine & Bank Adapters](#6-pluggable-finance-engine--bank-adapters)
7. [Live Market News & Daily Coffee Digest](#7-live-market-news--daily-coffee-digest)
8. [Local Plugin Development Guide](#8-local-plugin-development-guide)
9. [Automated Regression Test Suite](#9-automated-regression-test-suite)

---

## 1. Architecture Philosophy & Design Patterns

CRN is engineered from **First Principles** to operate as a lightweight, resource-controlled personal AI agent.

### Core Architectural Principles:
* **No Framework Bloat:** Implemented directly in clean Python 3.11+ using standard libraries, `httpx`, `asyncio`, and `sqlite3`. Zero reliance on AutoGen, CrewAI, or LangChain.
* **Separation of Concerns:** Core agent logic, storage schemas, finance engines, and web scrapers are strictly decoupled into independent modules (`app/agent/`, `app/finance/`, `app/core/`).
* **Privacy by Design:** Personal candidate resumes, private finance records, and local Obsidian notes live in `shared_workspace/` or `app/finance/plugins/` (both excluded via `.gitignore`).

---

## 2. Dual-Brain LLM Cascade Engine

CRN uses a **6-Tier Cascade Controller** (`app/agent/claw_logic.py`) to maximize intelligence while minimizing cloud API latency and cost.

```text
┌─────────────────────────────────────────────────────────────┐
│                 CRN LLM Cascade Controller                  │
└──────────────────────────────┬──────────────────────────────┘
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
 [Tier 1: Ollama]     [Tier 2: Gemini Lite]   [Tier 3: Gemini Pro]
 (gemma4 / qwen)     (gemini-3.5-flash-lite)  (gemini-3.0-pro)
```

### Cascade Resolution Sequence:
1. **Ollama Local Engine:** Checks for active local SLM instances (`gemma4:e4b` or `qwen2.5-coder`). Used for high-frequency queries when offline.
2. **Gemini Flash Lite:** Primary cloud worker for high-speed intent routing, job evaluation, and news sentiment scoring.
3. **Gemini Flash:** Secondary cloud worker for deep RAG synthesis and executive report generation.
4. **Gemini Pro:** Heavy reasoning fallback for complex multi-step analysis.

---

## 3. Universal 2-Tier Intent Router & Rate Limiter

Located in `app/agent/router.py`, the router parses user input into structured action steps:

```python
{"pipeline": [{"intent": "JOB_SCAN", "param": "", "explanation": "Run autonomous job scan"}]}
```

### Tier 1: Pattern-Matching Fast Paths
Instant regex matching for direct commands (`/menu`, `job scan`, `/finance`, `news scan`, `/ask`). Bypasses LLM inference for 0ms latency.

### Tier 2: LLM Intent Classifier
Free-text natural language prompts pass through `gemini-3.5-flash-lite` to extract complex multi-intent pipelines (e.g. *"do job scan, then check if I can afford 50k for snacks"*).

### Anti-Spam Rate Limiter:
Includes a sliding-window rate limiter per Telegram User ID (`max_req=6`, `window_sec=60`) to prevent API quota exhaustion.

---

## 4. 4-Tier RAG & Hybrid Vector Memory

Located in `app/agent/rag_logic.py`, the RAG engine combines lexical BM25 search with dense vector similarity embeddings.

### Search Engine Components:
1. **SQLite FTS5 Lexical Search:** Instant BM25 keyword matching across indexed web pages and vault notes.
2. **Dense Vector Embeddings (`gemini-embedding-001`):** 3072-dimensional vector embeddings stored in SQLite blob fields.
3. **Hybrid Reciprocal Rank Fusion (RRF):** Blends vector cosine similarity scores with FTS5 BM25 ranks to produce unified search context.

---

## 5. Autonomous Job Intelligence Pipeline

Located in `app/agent/job_crawler.py` and `app/agent/job_logic.py`:

* **Web Crawler:** Uses `async_playwright` to scrape target career boards defined in `targets.yaml`.
* **Dynamic Profile Evaluation:** Reads candidate credentials from `shared_workspace/user_profile.txt` to calculate match scores (0-100%) and 3-bullet pitch talking points.
* **PDF Report Generator:** Compiles job pipelines into styled PDF reports (`generate_job_pipeline_pdf()`).

---

## 6. Pluggable Finance Engine & Bank Adapters

Located in `app/finance/`:

* **Adapter Pattern Bank Email Parsers (`app/finance/parsers/`):**
  * `bni_parser.py`: Concrete IMAP adapter extracting QRIS payments and transfer notifications.
  * `bank_parser.example.py`: Open-source blueprint for BCA, Mandiri, BRI, or international banks.
* **Vault Markdown Sync:** Automatically formats monthly financial health snapshots into Obsidian vault notes (`financial_health_YYYY-MM.md`).

---

## 7. Live Market News & Daily Coffee Digest

Located in `app/agent/news_logic.py` and `app/agent/digest_logic.py`:

* Periodically scrapes tech and market headlines.
* Classifies sentiment using financial NLP models (`indo-roBERTa-financial-sentiment-v2`).
* Synthesizes bilingual Morning Coffee Digests delivered to Telegram and cached locally in `shared_workspace/`.

---

## 8. Local Plugin Development Guide

You can extend CRN locally by creating a subdirectory in `app/finance/plugins/my_plugin/`:

```python
# app/finance/plugins/my_plugin/__init__.py

def init_db(conn):
    """Create custom SQLite tables."""
    pass

def get_summary_metrics(conn) -> dict:
    """Return dictionary of financial metrics to blend into /finance."""
    return {"my_custom_metric": 100000.0}

def register_telegram_commands(app):
    """Mount custom slash commands into Telegram bot."""
    pass
```

---

## 9. Automated Regression Test Suite

Run the full automated test suite inside Docker:

```bash
docker exec claw_worker python3 /app/shared_workspace/test_crn_pipeline.py
```

*Coverage:* Verifies 21 core feature pipelines, database transactions, RAG indexing, router fast-paths, and report generation engines.
