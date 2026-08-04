"""
CRN Job Intelligence & Fit Analysis Engine
===========================================
Manages job opportunity tracking in SQLite DB, evaluates match score against candidate profile,
and generates tailored cover letter pitches.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.agent.claw_logic import summarize_with_cascade
from app.agent.rag_logic import search_personal_vault
from app.core.notifier import md_to_telegram_html, escape_html

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"
_DB_PATH = _WORKSPACE_DIR / "crn_intelligence.db"

# ---------------------------------------------------------------------------
# Master Profile Context (Dynamic User Profile Loader)
# ---------------------------------------------------------------------------
def get_user_profile_summary() -> str:
    """Load user profile from shared_workspace/user_profile.txt if exists, or return generic candidate profile."""
    local_profile = _WORKSPACE_DIR / "user_profile.txt"
    if local_profile.exists():
        try:
            return local_profile.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Failed to read user_profile.txt: %s", exc)

    return """Candidate Profile: Standard Candidate Profile
Degree: B.S. in Computer Science / Data Science / Software Engineering
Target Roles: AI Engineer, MLOps Engineer, Data Engineer, Software Engineer

Core Technical Capabilities:
• Machine Learning & LLM Systems: Fine-tuning, RAG pipelines, Prompt Engineering, PyTorch, HuggingFace.
• Data & Software Engineering: Python, SQL, Docker, FastAPI, pandas, PostgreSQL.
• Multi-Agent Systems & Cloud: Autonomous agent design, REST APIs, Cloud Infrastructure."""


# ---------------------------------------------------------------------------
# Database Schema
# ---------------------------------------------------------------------------
def init_jobs_db() -> None:
    """Ensure jobs_tracking table exists in crn_intelligence.db."""
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS jobs_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                remote_flag INTEGER DEFAULT 0,
                url TEXT NOT NULL,
                source_board TEXT NOT NULL,
                description TEXT,
                fit_score INTEGER NOT NULL DEFAULT 0,
                matched_skills_json TEXT,
                missing_skills_json TEXT,
                tailored_pitch TEXT,
                recommendation TEXT,
                status TEXT DEFAULT 'NEW',
                created_at TEXT NOT NULL
            )"""
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Fit Evaluation Engine
# ---------------------------------------------------------------------------
def evaluate_job_fit(title: str, company: str, location: str, description: str, url: str, source_board: str, model_override: str = "gemini-3.5-flash-lite") -> dict:
    """
    Evaluate job posting against Candidate profile AND dynamically retrieved notes
    from personal knowledge vault using LLM cascade.
    Returns structured dict with fit_score, matched_skills, missing_skills, tailored_pitch, and recommendation.
    """
    init_jobs_db()

    # 1. Dynamically retrieve top matching personal vault notes for this job description
    search_q = f"{title} {company} {description[:300]}"
    try:
        vault_items = search_personal_vault(search_q, top_k=3)
    except Exception as exc:
        logger.warning("Dynamic vault search for job fit failed: %s", exc)
        vault_items = []

    vault_blocks = []
    for idx, v in enumerate(vault_items, 1):
        vault_blocks.append(
            f"[{idx}] Note Title: {v['title']} ({v['rel_path']})\n"
            f"    Content Snippet: {v['content'][:500]}\n"
        )
    formatted_vault_notes = "\n".join(vault_blocks) if vault_blocks else "No specific vault notes retrieved."

    profile_summary = get_user_profile_summary()

    prompt = f"""You are a Senior Technical Recruiter & AI Career Strategist. Analyze the following job posting against Candidate Profile Baseline AND dynamically retrieved notes from personal Knowledge Vault.

Candidate Baseline Profile:
{profile_summary}

Dynamically Retrieved Notes from Knowledge Vault:
{formatted_vault_notes}

Job Posting Details:
• Title: {title}
• Company: {company}
• Location: {location}
• Board: {source_board}
• Description:
{description[:3000]}

Task:
Perform a strict, empirical fit evaluation. Respond ONLY in valid JSON format matching this exact schema:
{{
  "fit_score": <integer 0 to 100 representing overall compatibility>,
  "matched_skills": [<array of specific matching technical skills/tools>],
  "missing_skills": [<array of requirements mentioned in job description that candidate needs to highlight or adapt>],
  "tailored_pitch": "<3 bullet points in markdown connecting candidate's technical projects and retrieved notes directly to this job>",
  "recommendation": "<Strong Apply | Consider | Skip>"
}}
"""

    logger.info("Evaluating job fit for '%s' at %s via %s...", title, company, model_override)
    raw_response = summarize_with_cascade(prompt, "raw_prompt", model_override=model_override)

    # Extract JSON object from raw response
    try:
        start_idx = raw_response.find("{")
        end_idx = raw_response.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            json_str = raw_response[start_idx:end_idx]
            parsed = json.loads(json_str)
        else:
            raise ValueError("No JSON brackets found in response.")
    except Exception as exc:
        logger.warning("Failed to parse job evaluation JSON: %s. Raw: %s", exc, raw_response[:200])
        parsed = {
            "fit_score": 70,
            "matched_skills": ["Python", "Data Science", "AI/ML"],
            "missing_skills": ["Specific Domain Knowledge"],
            "tailored_pitch": "• High degree in Data Science (S.Si.D., Grade A).\n• Strong background in fine-tuning mT5 & RAG pipelines.\n• Built autonomous local agent architectures.",
            "recommendation": "Consider"
        }

    job_key = f"{company.lower().strip()}_{title.lower().strip()}"
    now_str = datetime.now(timezone.utc).isoformat()

    # Save to SQLite database
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO jobs_tracking
               (job_key, title, company, location, remote_flag, url, source_board, description,
                fit_score, matched_skills_json, missing_skills_json, tailored_pitch, recommendation, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_key,
                title,
                company,
                location,
                1 if "remote" in location.lower() or "remote" in title.lower() else 0,
                url,
                source_board,
                description[:2000],
                int(parsed.get("fit_score", 50)),
                json.dumps(parsed.get("matched_skills", [])),
                json.dumps(parsed.get("missing_skills", [])),
                parsed.get("tailored_pitch", ""),
                parsed.get("recommendation", "Consider"),
                now_str
            )
        )
        conn.commit()

    return {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "source_board": source_board,
        "fit_score": int(parsed.get("fit_score", 50)),
        "matched_skills": parsed.get("matched_skills", []),
        "missing_skills": parsed.get("missing_skills", []),
        "tailored_pitch": parsed.get("tailored_pitch", ""),
        "recommendation": parsed.get("recommendation", "Consider")
    }


def evaluate_job_batch(jobs: list[dict]) -> list[dict]:
    """
    Hybrid Batch Evaluation Mode: Evaluates a batch of up to 4 jobs in 1 single LLM call,
    incorporating dynamic Obsidian vault search for fast 15-second execution.
    """
    init_jobs_db()
    if not jobs:
        return []

    # 1. Dynamically retrieve top matching personal vault notes for this batch
    combined_query = " ".join([f"{j['title']} {j['company']}" for j in jobs])
    try:
        vault_items = search_personal_vault(combined_query, top_k=3)
    except Exception as exc:
        logger.warning("Dynamic vault search for job batch failed: %s", exc)
        vault_items = []

    vault_blocks = []
    for idx, v in enumerate(vault_items, 1):
        vault_blocks.append(
            f"[{idx}] Note Title: {v['title']} ({v['rel_path']})\n"
            f"    Content Snippet: {v['content'][:400]}\n"
        )
    formatted_vault_notes = "\n".join(vault_blocks) if vault_blocks else "No specific vault notes retrieved."

    jobs_text_blocks = []
    for idx, j in enumerate(jobs, 1):
        jobs_text_blocks.append(
            f"JOB [{idx}]:\n"
            f"  Title: {j['title']}\n"
            f"  Company: {j['company']}\n"
            f"  Location: {j['location']}\n"
            f"  Board: {j['source_board']}\n"
            f"  Description: {j.get('description', '')[:1200]}\n"
        )
    formatted_jobs = "\n---\n".join(jobs_text_blocks)

    profile_summary = get_user_profile_summary()

    prompt = f"""You are a Senior Technical Recruiter & AI Career Strategist. Evaluate the following batch of {len(jobs)} job postings against Candidate Profile Baseline AND dynamically retrieved notes from personal Knowledge Vault.

Candidate Baseline Profile:
{profile_summary}

Dynamically Retrieved Notes from Knowledge Vault:
{formatted_vault_notes}

Job Postings Batch ({len(jobs)} jobs):
{formatted_jobs}

Task:
Perform a strict empirical fit evaluation for each job. Respond ONLY in valid JSON format as a JSON array of objects matching this exact schema:
[
  {{
    "job_index": 1,
    "fit_score": <integer 0 to 100>,
    "matched_skills": [<array of matching technical skills>],
    "missing_skills": [<array of missing/needed skills>],
    "tailored_pitch": "<3 bullet points in markdown connecting candidate's technical projects and vault notes directly to this job>",
    "recommendation": "<Strong Apply | Consider | Skip>"
  }}
]
"""

    logger.info("Evaluating hybrid batch of %d jobs in 1 LLM call...", len(jobs))
    raw_response = summarize_with_cascade(prompt, "raw_prompt")

    parsed_list = []
    try:
        start_idx = raw_response.find("[")
        end_idx = raw_response.rfind("]") + 1
        if start_idx != -1 and end_idx != -1:
            json_str = raw_response[start_idx:end_idx]
            parsed_list = json.loads(json_str)
        else:
            raise ValueError("No JSON array brackets found.")
    except Exception as exc:
        logger.warning("Failed to parse batch JSON: %s. Using default structure.", exc)

    results = []
    now_str = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        for idx, job in enumerate(jobs, 1):
            match_data = {}
            for p in parsed_list:
                if p.get("job_index") == idx:
                    match_data = p
                    break
            if not match_data and idx <= len(parsed_list):
                match_data = parsed_list[idx - 1]

            fit_score = int(match_data.get("fit_score", 55))
            matched_skills = match_data.get("matched_skills", ["Python", "Data Science"])
            missing_skills = match_data.get("missing_skills", [])
            tailored_pitch = match_data.get("tailored_pitch", "• High degree in Data Science (S.Si.D., Grade A).")
            recommendation = match_data.get("recommendation", "Consider")

            job_key = f"{job['company'].lower().strip()}_{job['title'].lower().strip()}"
            cursor.execute(
                """INSERT OR REPLACE INTO jobs_tracking
                   (job_key, title, company, location, remote_flag, url, source_board, description,
                    fit_score, matched_skills_json, missing_skills_json, tailored_pitch, recommendation, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_key, job["title"], job["company"], job["location"],
                    1 if "remote" in job["location"].lower() or "remote" in job["title"].lower() else 0,
                    job["url"], job["source_board"], job.get("description", "")[:2000],
                    fit_score, json.dumps(matched_skills), json.dumps(missing_skills),
                    tailored_pitch, recommendation, now_str
                )
            )
            results.append({
                "title": job["title"],
                "company": job["company"],
                "location": job["location"],
                "url": job["url"],
                "source_board": job["source_board"],
                "fit_score": fit_score,
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                "tailored_pitch": tailored_pitch,
                "recommendation": recommendation
            })
        conn.commit()

    return results


# ---------------------------------------------------------------------------
# Database Queries & Formatting
# ---------------------------------------------------------------------------
def get_top_job_opportunities(min_fit: int = 50, limit: int = 10, status_filter: str = "NEW") -> list[dict]:
    """Retrieve top matched jobs from database sorted by fit_score with status filtering."""
    init_jobs_db()
    results = []
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        if status_filter == "ALL":
            cursor.execute(
                """SELECT id, title, company, location, url, source_board, fit_score,
                          matched_skills_json, missing_skills_json, tailored_pitch, recommendation, status, created_at
                   FROM jobs_tracking
                   WHERE fit_score >= ?
                   ORDER BY fit_score DESC, created_at DESC
                   LIMIT ?""",
                (min_fit, limit)
            )
        else:
            cursor.execute(
                """SELECT id, title, company, location, url, source_board, fit_score,
                          matched_skills_json, missing_skills_json, tailored_pitch, recommendation, status, created_at
                   FROM jobs_tracking
                   WHERE fit_score >= ? AND status = ?
                   ORDER BY fit_score DESC, created_at DESC
                   LIMIT ?""",
                (min_fit, status_filter, limit)
            )
        rows = cursor.fetchall()
        for row in rows:
            results.append({
                "id": row[0],
                "title": row[1],
                "company": row[2],
                "location": row[3],
                "url": row[4],
                "source_board": row[5],
                "fit_score": row[6],
                "matched_skills": json.loads(row[7] or "[]"),
                "missing_skills": json.loads(row[8] or "[]"),
                "tailored_pitch": row[9],
                "recommendation": row[10],
                "status": row[11],
                "created_at": row[12]
            })
    return results


def update_job_status(job_id: int, status: str) -> bool:
    """Update job application status (NEW, APPLIED, INTERVIEWING, OFFER, ARCHIVED)."""
    init_jobs_db()
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE jobs_tracking SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()
        return cursor.rowcount > 0


def format_job_digest_html(jobs: list[dict], max_items: int = 4) -> str:
    """Format job opportunities into a clean, size-constrained Telegram HTML message with accurate header count."""
    if not jobs:
        return "💼 <b>CRN Job Intelligence Engine</b>\n\nNo high-matching job opportunities recorded for this status filter. Tap <b>🚀 Run Job Scan</b> in /menu to scan target job boards!"

    entries = []
    for idx, j in enumerate(jobs[:max_items], 1):
        rec_badge = "🔥 Strong Apply" if j["fit_score"] >= 85 else ("⚡ Consider" if j["fit_score"] >= 70 else "📌 Potential")
        status_badge = f" [{j.get('status', 'NEW')}]" if j.get('status') and j['status'] != 'NEW' else ""
        matched_str = ", ".join(j["matched_skills"][:4]) if j["matched_skills"] else "Data Science, Python"
        
        entry = (
            f"<b>{idx}. {escape_html(j['title'])}</b> @ {escape_html(j['company'])}{status_badge}\n"
            f"   • <b>Match Score:</b> <code>{j['fit_score']}%</code> ({rec_badge})\n"
            f"   • <b>Location:</b> {escape_html(j['location'])} | <b>Board:</b> {escape_html(j['source_board'])}\n"
            f"   • <b>Matching Stack:</b> <i>{escape_html(matched_str)}</i>\n"
            f"   • <a href=\"{j['url']}\">🔗 Apply / View Job Posting</a>\n"
        )
        if j.get("tailored_pitch"):
            entry += f"   💡 <b>Tailored Talking Points:</b>\n{md_to_telegram_html(j['tailored_pitch'])}\n"
        entry += "------------------------------------\n"
        
        # Guard against Telegram 4096 char limit
        current_len = sum(len(e) for e in entries)
        if current_len + len(entry) > 3400:
            break
        entries.append(entry)

    header = f"💼 <b>CRN Job Intelligence Digest</b> — Top {len(entries)} Match(es)\n\n"
    return header + "\n".join(entries)


def sync_jobs_to_obsidian() -> Path:
    """
    Sync all tracked job opportunities from SQLite database into a vault markdown note.
    """
    from app.core.config import settings
    init_jobs_db()
    all_jobs = get_top_job_opportunities(min_fit=0, limit=100, status_filter="ALL")
    
    try:
        vault_dir = Path(settings.vault_raw_dir) / "jobs"
        vault_dir.mkdir(parents=True, exist_ok=True)
        file_path = vault_dir / "job_pipeline.md"
    except Exception as exc:
        logger.warning("Jobs vault dir %s not writable: %s. Using workspace fallback.", settings.vault_raw_dir, exc)
        file_path = _WORKSPACE_DIR / "job_pipeline.md"

    now_iso = datetime.now(timezone.utc).isoformat()
    lines = [
        "---",
        "tags: [crn, job-intelligence, career-pipeline, obsidian-vault]",
        "related: [\"[[job-pipeline]]\", \"[[career-opportunities]]\"]",
        f"updated: {now_iso[:10]}",
        "source: crn_intelligence.db",
        "---",
        "",
        "# 💼 CRN Job Intelligence & Career Pipeline",
        "",
        f"**Last Sync:** `{now_iso[:19].replace('T', ' ')} UTC` | **Total Tracked Opportunities:** `{len(all_jobs)}`",
        "",
        "## 📊 Executive Summary Table",
        "",
        "| ID | Status | Match Score | Position Title | Company | Location | Board | Link |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for j in all_jobs:
        status_flag = "🎯 NEW" if j['status'] == 'NEW' else ("✅ APPLIED" if j['status'] == 'APPLIED' else ("🎙️ INTERVIEW" if j['status'] == 'INTERVIEWING' else "📁 ARCHIVED"))
        lines.append(
            f"| `{j['id']}` | **{status_flag}** | `{j['fit_score']}%` | **{j['title']}** | {j['company']} | {j['location']} | {j['source_board']} | [Apply]({j['url']}) |"
        )

    lines.extend([
        "",
        "## 💡 Deep Tailored Talking Points",
        ""
    ])

    for j in [item for item in all_jobs if item['status'] != 'ARCHIVED'][:8]:
        lines.append(f"### {j['title']} — {j['company']} (`{j['fit_score']}% Match` | Status: `{j['status']}`)")
        lines.append(f"- **Location & Board:** {j['location']} ({j['source_board']})")
        lines.append(f"- **Matching Tech Stack:** {', '.join(j['matched_skills'])}")
        lines.append(f"- **Direct Link:** {j['url']}")
        if j.get('tailored_pitch'):
            lines.append("- **Talking Points:**")
            lines.append(j['tailored_pitch'])
        lines.append("")

    file_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Synced %d job opportunities to Obsidian vault: %s", len(all_jobs), file_path)
    return file_path


def generate_job_pipeline_pdf() -> Path:
    """
    Generate a clean, styled PDF report of the active career pipeline using ReportLab.
    Saved to shared_workspace/job_pipeline_report.pdf.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    init_jobs_db()
    jobs = get_top_job_opportunities(min_fit=0, limit=30, status_filter="ALL")
    pdf_path = _WORKSPACE_DIR / "job_pipeline_report.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0f172a')
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#475569')
    )
    h2_style = ParagraphStyle(
        'H2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#1e293b'),
        spaceBefore=10,
        spaceAfter=6
    )
    cell_style = ParagraphStyle(
        'Cell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#1e293b')
    )
    cell_bold = ParagraphStyle(
        'CellBold',
        parent=cell_style,
        fontName='Helvetica-Bold'
    )

    story = []
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M WIB')
    
    # Title Header
    story.append(Paragraph("💼 CRN Job Intelligence & Career Pipeline Report", title_style))
    story.append(Paragraph(f"Career Pipeline Report | Exported: {now_str}", subtitle_style))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#2563eb'), spaceAfter=12))

    # Summary Stats Table
    applied_cnt = sum(1 for j in jobs if j['status'] == 'APPLIED')
    new_cnt = sum(1 for j in jobs if j['status'] == 'NEW')
    interview_cnt = sum(1 for j in jobs if j['status'] == 'INTERVIEWING')
    
    stats_data = [
        [
            Paragraph(f"<b>Active Unapplied:</b> {new_cnt}", cell_bold),
            Paragraph(f"<b>Applied:</b> {applied_cnt}", cell_bold),
            Paragraph(f"<b>Interviewing:</b> {interview_cnt}", cell_bold),
            Paragraph(f"<b>Total Tracked:</b> {len(jobs)}", cell_bold)
        ]
    ]
    stats_table = Table(stats_data, colWidths=[130, 130, 130, 150])
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ALIGN', (0,0), (-1,-1), 'CENTER')
    ]))
    story.append(stats_table)
    story.append(Spacer(1, 12))

    # Main Pipeline Table
    story.append(Paragraph("📋 Job Opportunities Overview", h2_style))
    
    table_data = [
        [
            Paragraph("<b>#</b>", cell_bold),
            Paragraph("<b>Status</b>", cell_bold),
            Paragraph("<b>Score</b>", cell_bold),
            Paragraph("<b>Position Title</b>", cell_bold),
            Paragraph("<b>Company</b>", cell_bold),
            Paragraph("<b>Board</b>", cell_bold)
        ]
    ]

    for idx, j in enumerate(jobs[:25], 1):
        st = j['status']
        st_color = '#16a34a' if st == 'APPLIED' else ('#d97706' if st == 'INTERVIEWING' else ('#64748b' if st == 'ARCHIVED' else '#2563eb'))
        st_html = f"<font color=\"{st_color}\"><b>{st}</b></font>"
        
        table_data.append([
            Paragraph(str(idx), cell_style),
            Paragraph(st_html, cell_style),
            Paragraph(f"<b>{j['fit_score']}%</b>", cell_style),
            Paragraph(j['title'][:32], cell_style),
            Paragraph(j['company'][:25], cell_style),
            Paragraph(j['source_board'], cell_style)
        ])

    main_table = Table(table_data, colWidths=[25, 75, 45, 185, 120, 90])
    main_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('PADDING', (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')])
    ]))
    story.append(main_table)
    
    doc.build(story)
    logger.info("Generated PDF job pipeline report: %s", pdf_path)
    return pdf_path


def handle_job_status_nl_prompt(user_text: str) -> str:
    """
    Parse a natural language prompt from the user to update a job application status.
    Example: 'I applied to PT Karisma Fullstack AI Engineer' or 'Archive Terra Softech'.
    """
    init_jobs_db()
    all_jobs = get_top_job_opportunities(min_fit=0, limit=50, status_filter="ALL")
    if not all_jobs:
        return "⚠️ No jobs recorded in database to update."

    jobs_summary = "\n".join([f"ID: {j['id']} | Title: {j['title']} | Company: {j['company']} | Status: {j['status']}" for j in all_jobs[:20]])

    prompt = f"""You are an AI Career Assistant. The user sent a natural language update about their job application status.

User Input: "{user_text}"

Current Tracked Jobs in Database:
{jobs_summary}

Task:
Identify which job in the database the user is referencing and what status they want to set it to.
Valid status values: "APPLIED", "INTERVIEWING", "OFFER", "ARCHIVED", "NEW".

Respond ONLY in valid JSON:
{{
  "matched_job_id": <integer ID of matched job, or null if no match found>,
  "new_status": "<APPLIED | INTERVIEWING | OFFER | ARCHIVED | NEW>",
  "explanation": "<short confirmation text explaining the status change>"
}}
"""

    raw_res = summarize_with_cascade(prompt, "raw_prompt", model_override="gemini-3.5-flash-lite")
    try:
        start_idx = raw_res.find("{")
        end_idx = raw_res.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            data = json.loads(raw_res[start_idx:end_idx])
            job_id = data.get("matched_job_id")
            new_status = data.get("new_status", "APPLIED").upper()
            explanation = data.get("explanation", f"Updated status to {new_status}.")

            if job_id and update_job_status(job_id, new_status):
                sync_jobs_to_obsidian()
                return f"✅ <b>Job Status Updated!</b>\n\n{escape_html(explanation)}\n\n📁 <i>Synced to Obsidian vault: raw/crn/jobs/job_pipeline.md</i>"
    except Exception as exc:
        logger.warning("NL job status parse error: %s", exc)

    return "⚠️ Could not identify which job you wanted to update. Try specifying the company or position title (e.g. <i>'Applied to Karisma AI Engineer'</i>)."
