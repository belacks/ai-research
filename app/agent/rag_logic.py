"""
app/agent/rag_logic.py
======================
RAG & Temporal Vector Memory Engine for Ceros Research Node (CRN).

Features:
  1. 4-Tier Embedding Cascade:
     • Tier 1: Cloud Gemini text-embedding-004 (with RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY task_type)
     • Tier 2: Cloud Gemini embedding-001
     • Tier 3: Local Ollama /api/embeddings (nomic-embed-text / target model)
     • Tier 4: SQLite FTS5 BM25 native text search (failsafe)
  2. Hybrid Retrieval: Vector Cosine Similarity + FTS5 Keyword Matching
  3. Grounded Synthesis: 6-Tier LLM Cascade with citations linking to titles, dates, and URLs.
"""

import json
import logging
import sqlite3
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.agent.claw_logic import summarize_with_cascade
from app.core.config import settings
from app.core.notifier import md_to_telegram_html, send_telegram_alert, escape_html

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"
_DB_PATH = _WORKSPACE_DIR / "crn_intelligence.db"


# ---------------------------------------------------------------------------
# Database & FTS5 Initialization
# ---------------------------------------------------------------------------
def _init_rag_db() -> None:
    """Ensure vector embeddings and FTS5 tables exist in crn_intelligence.db for both Web Intelligence and Personal Vault."""
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 1. Web Intelligence Vector Embeddings Table
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS intelligence_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL UNIQUE,
                url TEXT,
                title TEXT,
                content TEXT,
                model_name TEXT NOT NULL,
                task_type TEXT,
                dim_size INTEGER NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )

        # 2. Web Intelligence FTS5 Table
        try:
            cursor.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS intelligence_fts USING fts5(
                    item_id, item_type, title, content, url
                )"""
            )
        except sqlite3.OperationalError:
            pass

        # 3. Personal Obsidian Vault Vector Embeddings Table
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS personal_vault_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                rel_path TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                model_name TEXT NOT NULL,
                task_type TEXT,
                dim_size INTEGER NOT NULL,
                embedding_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )

        # 4. Personal Obsidian Vault FTS5 Table
        try:
            cursor.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS personal_vault_fts USING fts5(
                    file_path, rel_path, title, content
                )"""
            )
        except sqlite3.OperationalError:
            pass


# ---------------------------------------------------------------------------
# 4-Tier Embedding Cascade
# ---------------------------------------------------------------------------
def generate_embedding(text: str, is_query: bool = False) -> tuple[list[float], str, str]:
    """
    Generate vector embeddings using an empirically verified 4-tier cascade:
      Tier 1: Gemini gemini-embedding-001 (RETRIEVAL_QUERY if is_query else RETRIEVAL_DOCUMENT)
      Tier 2: Gemini gemini-embedding-2
      Tier 3: Local Ollama /api/embeddings
      Tier 4: Empty list fallback (triggers SQLite FTS5 BM25 text search)

    Returns tuple: (embedding_vector, model_name, task_type_used)
    """
    task_type = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"

    # Tier 1: Gemini gemini-embedding-001 (Supports explicit RETRIEVAL_DOCUMENT & RETRIEVAL_QUERY)
    if settings.gemini_api_key:
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=settings.gemini_api_key)
            logger.info("Requesting Tier 1 Embedding: gemini-embedding-001 (task_type=%s)...", task_type)
            
            res = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type)
            )
            if res and res.embeddings and len(res.embeddings) > 0:
                vector = res.embeddings[0].values
                logger.info("✅ Tier 1 Embedding generated successfully (%d dimensions).", len(vector))
                return list(vector), "gemini-embedding-001", task_type
        except Exception as exc:
            logger.warning("Tier 1 Embedding (gemini-embedding-001) failed: %s. Trying Tier 2...", exc)

    # Tier 2: Gemini gemini-embedding-2 (Latest 3072-dim model)
    if settings.gemini_api_key:
        try:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            logger.info("Requesting Tier 2 Embedding: gemini-embedding-2...")
            
            prompt_text = f"task: {task_type.lower()} | content: {text}"
            res = client.models.embed_content(
                model="gemini-embedding-2",
                contents=prompt_text
            )
            if res and res.embeddings and len(res.embeddings) > 0:
                vector = res.embeddings[0].values
                logger.info("✅ Tier 2 Embedding generated successfully (%d dimensions).", len(vector))
                return list(vector), "gemini-embedding-2", task_type
        except Exception as exc:
            logger.warning("Tier 2 Embedding (gemini-embedding-2) failed: %s. Trying Tier 3...", exc)

    # Tier 3: Local Ollama Embedding
    try:
        ollama_url = f"{settings.ollama_base_url}/api/embeddings"
        target_model = settings.target_model
        logger.info("Requesting Tier 3 Embedding: Ollama %s...", target_model)
        
        payload = {"model": target_model, "prompt": text}
        resp = requests.post(ollama_url, json=payload, timeout=3)
        if resp.ok:
            vector = resp.json().get("embedding", [])
            if vector:
                logger.info("✅ Tier 3 Embedding generated successfully via Ollama (%d dimensions).", len(vector))
                return list(vector), f"ollama-{target_model}", "LOCAL_OLLAMA"
    except Exception as exc:
        logger.warning("Tier 3 Embedding (Ollama) failed: %s. Falling back to Tier 4 FTS5...", exc)

    # Tier 4: Fallback (Empty vector triggers SQLite FTS5 keyword BM25 search)
    logger.info("Tier 4: Using SQLite FTS5 BM25 text search failsafe.")
    return [], "sqlite-fts5", "BM25"


# ---------------------------------------------------------------------------
# Indexing Intelligence Records into Vector & FTS Tables
# ---------------------------------------------------------------------------
def sync_intelligence_index() -> int:
    """
    Scan processed_news and briefing_items tables in SQLite and index any unindexed items.
    Returns number of newly indexed records.
    """
    _init_rag_db()
    new_indexed = 0
    now_str = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()

        # 1. Fetch unindexed processed_news items
        cursor.execute(
            """SELECT url, title, sentiment, confidence, ticker, processed_at
               FROM processed_news"""
        )
        news_rows = cursor.fetchall()
        for row in news_rows:
            url, title, sentiment, confidence, ticker, proc_at = row
            item_id = f"news_{url}"
            
            # Check if already indexed
            cursor.execute("SELECT 1 FROM intelligence_embeddings WHERE item_id = ?", (item_id,))
            if cursor.fetchone():
                continue

            content = f"[{ticker}] [{sentiment.upper()}] {title}"
            vec, model_name, task_type = generate_embedding(content, is_query=False)

            cursor.execute(
                """INSERT OR REPLACE INTO intelligence_embeddings 
                   (item_type, item_id, url, title, content, model_name, task_type, dim_size, embedding_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("news", item_id, url, title, content, model_name, task_type, len(vec), json.dumps(vec), now_str)
            )

            # Insert into FTS index
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO intelligence_fts (item_id, item_type, title, content, url) VALUES (?, ?, ?, ?, ?)",
                    (item_id, "news", title, content, url)
                )
            except sqlite3.OperationalError:
                pass

            new_indexed += 1

        # 2. Fetch unindexed briefing_items
        try:
            cursor.execute(
                """SELECT title, url, summary, key_insights, relevance_score, category, crawled_at
                   FROM briefing_items"""
            )
            briefing_rows = cursor.fetchall()
            for row in briefing_rows:
                title, url, summary, key_insights, score, cat, crawled_at = row
                item_id = f"briefing_{url}_{crawled_at}"

                cursor.execute("SELECT 1 FROM intelligence_embeddings WHERE item_id = ?", (item_id,))
                if cursor.fetchone():
                    continue

                content = f"[{cat}] {title}. Summary: {summary}. Insights: {key_insights}"
                vec, model_name, task_type = generate_embedding(content, is_query=False)

                cursor.execute(
                    """INSERT OR REPLACE INTO intelligence_embeddings 
                       (item_type, item_id, url, title, content, model_name, task_type, dim_size, embedding_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("briefing", item_id, url, title, content, model_name, task_type, len(vec), json.dumps(vec), now_str)
                )

                try:
                    cursor.execute(
                        "INSERT OR REPLACE INTO intelligence_fts (item_id, item_type, title, content, url) VALUES (?, ?, ?, ?, ?)",
                        (item_id, "briefing", title, content, url)
                    )
                except sqlite3.OperationalError:
                    pass

                new_indexed += 1
        except sqlite3.OperationalError:
            pass

    if new_indexed > 0:
        logger.info("Indexed %d new intelligence items into RAG vector memory.", new_indexed)
    return new_indexed


# ---------------------------------------------------------------------------
# Hybrid Retrieval & Cosine Similarity Ranking
# ---------------------------------------------------------------------------
def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity dot product between two vector lists."""
    v1 = np.array(vec1, dtype=np.float32)
    v2 = np.array(vec2, dtype=np.float32)
    if v1.shape != v2.shape or np.linalg.norm(v1) == 0 or np.linalg.norm(v2) == 0:
        return 0.0
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))


def search_intelligence(query_text: str, top_k: int = 5) -> list[dict]:
    """
    Search past intelligence items using hybrid vector cosine similarity + FTS5 keyword matching.
    """
    sync_intelligence_index()
    
    query_vec, model_used, task_used = generate_embedding(query_text, is_query=True)
    results = []

    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()

        if query_vec and len(query_vec) > 0:
            # Vector Cosine Similarity Search
            cursor.execute(
                """SELECT item_id, item_type, title, content, url, embedding_json, dim_size, created_at
                   FROM intelligence_embeddings"""
            )
            rows = cursor.fetchall()
            
            scored_items = []
            for row in rows:
                item_id, item_type, title, content, url, vec_json, dim_size, created_at = row
                doc_vec = json.loads(vec_json)
                
                # Check vector dimension compatibility
                if len(doc_vec) == len(query_vec):
                    sim_score = _cosine_similarity(query_vec, doc_vec)
                    scored_items.append({
                        "item_id": item_id,
                        "item_type": item_type,
                        "title": title,
                        "content": content,
                        "url": url,
                        "score": sim_score,
                        "created_at": created_at
                    })

            scored_items.sort(key=lambda x: x["score"], reverse=True)
            results = scored_items[:top_k]

        # FTS5 BM25 Fallback or Keyword Augmentation
        if not results:
            logger.info("Vector search returned empty results. Running FTS5 keyword search...")
            try:
                # Clean query string for FTS syntax
                clean_q = "".join(c for c in query_text if c.isalnum() or c.isspace())
                cursor.execute(
                    """SELECT item_id, item_type, title, content, url
                       FROM intelligence_fts
                       WHERE intelligence_fts MATCH ?
                       LIMIT ?""",
                    (clean_q, top_k)
                )
                fts_rows = cursor.fetchall()
                for row in fts_rows:
                    results.append({
                        "item_id": row[0],
                        "item_type": row[1],
                        "title": row[2],
                        "content": row[3],
                        "url": row[4],
                        "score": 0.85,
                        "created_at": "FTS Match"
                    })
            except Exception as exc:
                logger.warning("FTS5 query failed: %s", exc)

    return results


# ---------------------------------------------------------------------------
# Personal Obsidian Vault Indexing & Search
# ---------------------------------------------------------------------------
def sync_personal_vault_index() -> int:
    """
    Recursively scan personal vault for .md notes
    and index any new/modified files into personal_vault_embeddings and personal_vault_fts.
    """
    _init_rag_db()
    vault_root = Path(getattr(settings, "obsidian_vault_dir", "./shared_workspace/vault_output"))
    if not vault_root.exists():
        logger.warning("Obsidian vault directory %s does not exist.", vault_root)
        return 0

    target_subdirs = ["wiki", "journal", "outputs", "raw"]
    new_indexed = 0
    now_str = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()

        for subdir in target_subdirs:
            dir_path = vault_root / subdir
            if not dir_path.exists():
                continue

            for md_file in dir_path.rglob("*.md"):
                file_str = str(md_file.resolve())
                rel_str = str(md_file.relative_to(vault_root))
                
                mtime_str = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc).isoformat()

                # Check if already indexed with same modification timestamp
                cursor.execute(
                    "SELECT updated_at FROM personal_vault_embeddings WHERE file_path = ?",
                    (file_str,)
                )
                row = cursor.fetchone()
                if row and row[0] == mtime_str:
                    continue

                try:
                    raw_text = md_file.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", md_file, exc)
                    continue

                if len(raw_text) < 30:
                    continue

                title = md_file.stem.replace("-", " ").replace("_", " ").title()
                embed_text = f"[{rel_str}] {title}\n{raw_text[:1500]}"
                vec, model_name, task_type = generate_embedding(embed_text, is_query=False)

                cursor.execute(
                    """INSERT OR REPLACE INTO personal_vault_embeddings
                       (file_path, rel_path, title, content, model_name, task_type, dim_size, embedding_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (file_str, rel_str, title, raw_text[:3000], model_name, task_type, len(vec), json.dumps(vec), mtime_str)
                )

                try:
                    cursor.execute(
                        "INSERT OR REPLACE INTO personal_vault_fts (file_path, rel_path, title, content) VALUES (?, ?, ?, ?)",
                        (file_str, rel_str, title, raw_text[:3000])
                    )
                except sqlite3.OperationalError:
                    pass

                new_indexed += 1

    if new_indexed > 0:
        logger.info("Indexed %d new/modified notes from personal Obsidian vault into RAG memory.", new_indexed)
    return new_indexed


def search_personal_vault(query_text: str, top_k: int = 5) -> list[dict]:
    """
    Search personal Obsidian vault notes using hybrid vector cosine similarity + FTS5 keyword search.
    """
    sync_personal_vault_index()
    query_vec, model_used, task_used = generate_embedding(query_text, is_query=True)
    results = []

    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()

        if query_vec and len(query_vec) > 0:
            cursor.execute(
                """SELECT file_path, rel_path, title, content, embedding_json, dim_size, updated_at
                   FROM personal_vault_embeddings"""
            )
            rows = cursor.fetchall()
            scored_items = []
            for row in rows:
                file_path, rel_path, title, content, vec_json, dim_size, updated_at = row
                doc_vec = json.loads(vec_json)
                if len(doc_vec) == len(query_vec):
                    sim_score = _cosine_similarity(query_vec, doc_vec)
                    scored_items.append({
                        "file_path": file_path,
                        "rel_path": rel_path,
                        "title": title,
                        "content": content[:800],
                        "score": sim_score,
                        "updated_at": updated_at
                    })
            scored_items.sort(key=lambda x: x["score"], reverse=True)
            results = scored_items[:top_k]

        if not results:
            try:
                clean_q = "".join(c for c in query_text if c.isalnum() or c.isspace())
                cursor.execute(
                    """SELECT file_path, rel_path, title, content
                       FROM personal_vault_fts
                       WHERE personal_vault_fts MATCH ?
                       LIMIT ?""",
                    (clean_q, top_k)
                )
                fts_rows = cursor.fetchall()
                for row in fts_rows:
                    results.append({
                        "file_path": row[0],
                        "rel_path": row[1],
                        "title": row[2],
                        "content": row[3][:800],
                        "score": 0.85,
                        "updated_at": "FTS Match"
                    })
            except Exception as exc:
                logger.warning("Personal vault FTS5 query failed: %s", exc)

    return results


# ---------------------------------------------------------------------------
# RAG Answer Synthesis & Telegram Dispatch
# ---------------------------------------------------------------------------
def ask_second_brain(query_text: str, scope: str = "web", model_override: str = None) -> str:
    """
    RAG Pipeline: Dual-brain scope search.
    scope='web' for external CRN DB, scope='vault' for personal Obsidian vault, scope='all' for both.
    """
    logger.info("Executing RAG Query (/ask scope=%s): '%s'", scope, query_text)
    
    if scope == "all":
        vault_items = search_personal_vault(query_text, top_k=3)
        web_items = search_intelligence(query_text, top_k=3)

        if not vault_items and not web_items:
            return f"ℹ️ No relevant personal vault notes or web intelligence records found for: <i>\"{escape_html(query_text)}\"</i>"

        context_blocks = []
        if vault_items:
            context_blocks.append("=== PERSONAL OBSIDIAN VAULT NOTES ===")
            for idx, item in enumerate(vault_items, 1):
                context_blocks.append(
                    f"[{idx}] Note: {item['title']} (File: {item['rel_path']})\n"
                    f"    Snippet: {item['content']}\n"
                )
        if web_items:
            context_blocks.append("=== WEB INTELLIGENCE RECORDS ===")
            for idx, item in enumerate(web_items, 1):
                context_blocks.append(
                    f"[{idx}] Title: {item['title']} (URL: {item['url']})\n"
                    f"    Content: {item['content']}\n"
                )
        formatted_context = "\n---\n".join(context_blocks)

        rag_prompt = f"""You are an AI Research & Knowledge Assistant. Below is a unified set of notes from personal knowledge vault and past web crawl records.

User Question: "{query_text}"

Retrieved Context:
{formatted_context}

Instructions:
1. Provide a sharp, direct, technically substantive synthesis answering the User Question based on the context above.
2. Structure into 3 sections (casual peer tone, no em-dashes):
   • 🧠 **Unified Knowledge Synthesis** (Direct 2-3 sentence answer)
   • 💡 **Key Takeaways & Evidence** (Bullet points referencing specific findings)
   • 📌 **Sources & Notes Cited** (List titles, paths, and URLs retrieved)
"""
        scope_title = "🧠 Unified Knowledge & Web Search"
    elif scope == "vault":
        retrieved_items = search_personal_vault(query_text, top_k=5)
        if not retrieved_items:
            return f"ℹ️ No relevant personal vault notes (wiki/journal/outputs) found for: <i>\"{escape_html(query_text)}\"</i>"

        context_blocks = []
        for idx, item in enumerate(retrieved_items, 1):
            context_blocks.append(
                f"[{idx}] Note Title: {item['title']} (File: {item['rel_path']})\n"
                f"    Content Snippet: {item['content']}\n"
            )
        formatted_context = "\n---\n".join(context_blocks)

        rag_prompt = f"""You are an AI Knowledge Assistant. Below are notes retrieved from personal knowledge vault to answer the user's question.

User Question: "{query_text}"

Retrieved Personal Vault Notes:
{formatted_context}

Instructions:
1. Provide a direct, technically substantive synthesis answering the User Question based strictly on personal vault notes above.
2. Structure into 3 sections (casual peer tone, no em-dashes):
   • 🧠 **Personal Knowledge Answer** (Direct 2-3 sentence answer)
   • 💡 **Key Takeaways & Details** (Bullet points referencing specific notes)
   • 📌 **Vault Notes Referenced** (List note titles and relative paths)
"""
        scope_title = "🧠 Personal Vault Search"
    else:
        retrieved_items = search_intelligence(query_text, top_k=5)
        if not retrieved_items:
            return f"ℹ️ No relevant web intelligence records found for: <i>\"{escape_html(query_text)}\"</i>"

        context_blocks = []
        for idx, item in enumerate(retrieved_items, 1):
            context_blocks.append(
                f"[{idx}] Title: {item['title']}\n"
                f"    Category/Type: {item['item_type'].upper()}\n"
                f"    Source URL: {item['url']}\n"
                f"    Content: {item['content']}\n"
            )
        formatted_context = "\n---\n".join(context_blocks)

        rag_prompt = f"""You are Ceros's AI Intelligence Lead. Below is a set of past web crawl records retrieved from CRN's database to answer the user's research query.

User Question: "{query_text}"

Retrieved Intelligence Context:
{formatted_context}

Instructions:
1. Provide a sharp, direct, executive synthesis answering the User Question based strictly on the retrieved context above.
2. Structure your response into 3 sections (casual but technically substantive tone, no em-dashes):
   • 🌐 **Executive Web Answer** (Direct 2-3 sentence answer)
   • 💡 **Key Evidence & Takeaways** (Bullet points referencing specific findings)
   • 📌 **Sources Cited** (List titles and URLs retrieved)
"""
        scope_title = "🌐 Web Intelligence Search"

    logger.info("Synthesizing RAG answer via 6-tier LLM cascade for %s...", scope_title)
    answer_text = summarize_with_cascade(rag_prompt, "raw_prompt", model_override=model_override)

    header = f"<b>{scope_title}</b>\n<b>Query:</b> <i>\"{escape_html(query_text)}\"</i>\n\n"
    telegram_html = header + md_to_telegram_html(answer_text)
    return telegram_html
