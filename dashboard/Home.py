"""
OpenClaw Research Node — Command Center Dashboard
===================================================
Streamlit entry-point for the `dashboard` Docker service.

Run locally:
    streamlit run dashboard/Home.py

Reads Markdown briefings from shared_workspace/ and provides a
manual trigger mechanism for the research agent.
"""

import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Ceros Research Command Center",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"

# Ensure the directory exists (first run / fresh clone)
_WORKSPACE_DIR.mkdir(exist_ok=True)

# Important: Streamlit runs from /app/dashboard, so /app is usually not in sys.path.
import sys
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Custom CSS — dark ops command-center aesthetic
# ---------------------------------------------------------------------------
def _inject_css() -> None:
    st.markdown(
        """
        <style>
        /* ---------- Import Google Font ---------- */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

        /* ---------- Root variables ---------- */
        :root {
            --bg-primary:   #0a0e17;
            --bg-card:      #111827;
            --bg-card-alt:  #1a2236;
            --border:       #1e2a3a;
            --accent:       #3b82f6;
            --accent-glow:  rgba(59, 130, 246, 0.15);
            --success:      #10b981;
            --warning:      #f59e0b;
            --text-primary: #e2e8f0;
            --text-muted:   #64748b;
            --mono:         'JetBrains Mono', monospace;
            --sans:         'Inter', sans-serif;
        }

        /* ---------- Global ---------- */
        .stApp {
            background-color: var(--bg-primary) !important;
            font-family: var(--sans);
        }

        /* ---------- Header banner ---------- */
        .cmd-header {
            background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 2rem 2.5rem;
            margin-bottom: 1.5rem;
            position: relative;
            overflow: hidden;
        }
        .cmd-header::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--accent), transparent);
        }
        .cmd-header h1 {
            font-family: var(--sans);
            font-weight: 700;
            font-size: 1.75rem;
            color: #f8fafc;
            margin: 0 0 0.25rem 0;
            letter-spacing: -0.02em;
        }
        .cmd-header .subtitle {
            font-family: var(--mono);
            font-size: 0.8rem;
            color: var(--text-muted);
            margin: 0;
        }

        /* ---------- Status pill ---------- */
        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-family: var(--mono);
            font-size: 0.75rem;
            padding: 4px 12px;
            border-radius: 9999px;
            background: rgba(16, 185, 129, 0.1);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.25);
        }
        .status-pill .dot {
            width: 6px; height: 6px;
            border-radius: 50%;
            background: var(--success);
            animation: pulse-dot 2s ease-in-out infinite;
        }
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; }
            50%      { opacity: 0.3; }
        }

        /* ---------- Tab styling ---------- */
        .stTabs [data-baseweb="tab-list"] {
            gap: 0;
            background: var(--bg-card);
            border-radius: 8px;
            padding: 4px;
            border: 1px solid var(--border);
        }
        .stTabs [data-baseweb="tab"] {
            font-family: var(--mono);
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-muted);
            border-radius: 6px;
            padding: 0.5rem 1.25rem;
        }
        .stTabs [aria-selected="true"] {
            background: var(--accent-glow) !important;
            color: var(--accent) !important;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }
        .stTabs [data-baseweb="tab-highlight"] {
            display: none;
        }
        .stTabs [data-baseweb="tab-border"] {
            display: none;
        }

        /* ---------- Card container ---------- */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        }
        .card-title {
            font-family: var(--mono);
            font-size: 0.7rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            margin-bottom: 0.75rem;
        }

        /* ---------- Briefing content ---------- */
        .briefing-meta {
            font-family: var(--mono);
            font-size: 0.75rem;
            color: var(--text-muted);
            padding: 0.5rem 0.75rem;
            background: var(--bg-card-alt);
            border-radius: 6px;
            margin-bottom: 1rem;
            border-left: 3px solid var(--accent);
        }

        /* ---------- File list ---------- */
        .file-entry {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.65rem 0.85rem;
            background: var(--bg-card-alt);
            border-radius: 6px;
            margin-bottom: 0.4rem;
            border: 1px solid transparent;
            transition: border-color 0.2s ease;
        }
        .file-entry:hover {
            border-color: var(--border);
        }
        .file-name {
            font-family: var(--mono);
            font-size: 0.8rem;
            color: var(--text-primary);
        }
        .file-date {
            font-family: var(--mono);
            font-size: 0.7rem;
            color: var(--text-muted);
        }

        /* ---------- Empty state ---------- */
        .empty-state {
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-muted);
        }
        .empty-state .icon {
            font-size: 2.5rem;
            margin-bottom: 0.75rem;
        }
        .empty-state p {
            font-family: var(--mono);
            font-size: 0.85rem;
        }

        /* ---------- Mission button ---------- */
        .stButton > button {
            font-family: var(--mono) !important;
            font-weight: 500 !important;
            border: 1px solid var(--accent) !important;
            background: var(--accent-glow) !important;
            color: var(--accent) !important;
            border-radius: 8px !important;
            padding: 0.6rem 1.5rem !important;
            transition: all 0.25s ease !important;
        }
        .stButton > button:hover {
            background: var(--accent) !important;
            color: #fff !important;
            box-shadow: 0 0 20px rgba(59, 130, 246, 0.3) !important;
        }

        /* ---------- Misc overrides ---------- */
        .stMarkdown, .stMarkdown p { color: var(--text-primary); }
        hr { border-color: var(--border) !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _fetch_ollama_models() -> list[str]:
    import requests
    from app.core.config import settings
    try:
        resp = requests.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            return [m.get("name") for m in resp.json().get("models", [])]
    except Exception:
        pass
    return []

def _get_md_files() -> list[tuple[Path, float]]:
    """
    Return a list of (path, mtime) tuples for every .md file in
    shared_workspace/, sorted newest-first.
    """
    if not _WORKSPACE_DIR.exists():
        return []

    files = [
        (p, p.stat().st_mtime)
        for p in _WORKSPACE_DIR.glob("*.md")
        if p.is_file()
    ]
    files.sort(key=lambda t: t[1], reverse=True)
    return files


def _fmt_timestamp(epoch: float) -> str:
    """Format an epoch timestamp into a human-readable WIB string."""
    wib = timezone(timedelta(hours=7), name="WIB")
    dt = datetime.fromtimestamp(epoch, tz=wib)
    return dt.strftime("%Y-%m-%d %H:%M:%S WIB")


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

def _render_header() -> None:
    st.markdown(
        """
        <div class="cmd-header">
            <h1>🔬 Ceros Research · Command Center</h1>
            <p class="subtitle">Autonomous Research Agent — Local Intelligence Node</p>
            <div style="margin-top: 0.75rem;">
                <span class="status-pill">
                    <span class="dot"></span>
                    NODE ONLINE
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_briefing_tab() -> None:
    """Executive Briefing — show the latest .md file from shared_workspace/."""
    md_files = _get_md_files()

    if not md_files:
        st.markdown(
            """
            <div class="empty-state">
                <div class="icon">📡</div>
                <p>No briefings yet.<br>
                Waiting for the research agent to produce its first report …</p>
                <p style="font-size:0.7rem; margin-top:0.5rem; color:#475569;">
                    Reports will appear in <code>shared_workspace/*.md</code>
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    latest_path, latest_mtime = md_files[0]

    # Meta bar
    st.markdown(
        f"""
        <div class="briefing-meta">
            📄 <strong>{latest_path.name}</strong>
            &nbsp;·&nbsp; last modified {_fmt_timestamp(latest_mtime)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Render markdown content
    content = latest_path.read_text(encoding="utf-8")
    st.markdown(content)

    # Sidebar: all available files
    if len(md_files) > 1:
        st.markdown("---")
        st.markdown(
            '<div class="card-title">📂 All Briefings</div>',
            unsafe_allow_html=True,
        )
        for path, mtime in md_files:
            st.markdown(
                f"""
                <div class="file-entry">
                    <span class="file-name">{path.name}</span>
                    <span class="file-date">{_fmt_timestamp(mtime)}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_mission_tab() -> None:
    """Mission Control — manual trigger and node status."""

    col_left, col_right = st.columns([2, 1], gap="large")

    with col_left:
        st.markdown(
            '<div class="card-title">⚡ Manual Trigger</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            """
            <div class="card">
                <p style="font-size:0.85rem;">
                    Queue an immediate research cycle by sending a trigger signal.
                    The research agent will pick up the signal on its next heartbeat
                    (≤60 s).
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        from app.core.config import settings
        default_model = settings.target_model
        
        models = _fetch_ollama_models()
        if not models:
            models = [default_model]
        if default_model not in models:
            models.append(default_model)
        
        selected_model = st.selectbox("LLM Engine", models, index=models.index(default_model))

        if st.button("🚀  Run Research Task Now", key="btn_trigger", use_container_width=True):
            trigger_path = _WORKSPACE_DIR / "trigger.txt"
            try:
                trigger_path.write_text(selected_model, encoding="utf-8")
                st.toast(f"✅ Trigger signal sent (Model: {selected_model})", icon="🚀")
            except Exception as exc:
                st.error(f"Failed to send trigger signal: {exc}")

    with col_right:
        st.markdown(
            '<div class="card-title">📊 Node Status</div>',
            unsafe_allow_html=True,
        )

        # Trigger file status
        trigger_exists = (_WORKSPACE_DIR / "trigger.txt").exists()
        trigger_label = "PENDING" if trigger_exists else "IDLE"
        trigger_color = "var(--warning)" if trigger_exists else "var(--success)"

        # Last run info
        last_run_file = _PROJECT_ROOT / "app" / "scheduler" / "last_run.json"
        if last_run_file.exists():
            import json
            try:
                data = json.loads(last_run_file.read_text(encoding="utf-8"))
                last_run_utc = data.get("last_run_utc", "—")
                if last_run_utc != "—":
                    dt_utc = datetime.fromisoformat(last_run_utc)
                    wib = timezone(timedelta(hours=7), name="WIB")
                    last_run_text = dt_utc.astimezone(wib).strftime("%Y-%m-%d %H:%M:%S WIB")
                else:
                    last_run_text = "—"
            except Exception:
                last_run_text = "corrupt"
        else:
            last_run_text = "never"

        # Briefing count
        briefing_count = len(_get_md_files())

        st.markdown(
            f"""
            <div class="card" style="font-family: var(--mono); font-size: 0.8rem;">
                <div style="margin-bottom: 0.6rem;">
                    <span style="color: var(--text-muted);">Trigger Queue</span><br>
                    <span style="color: {trigger_color}; font-weight: 600;">● {trigger_label}</span>
                </div>
                <div style="margin-bottom: 0.6rem;">
                    <span style="color: var(--text-muted);">Last Agent Run</span><br>
                    <span style="color: var(--text-primary);">{last_run_text}</span>
                </div>
                <div>
                    <span style="color: var(--text-muted);">Briefings on File</span><br>
                    <span style="color: var(--text-primary);">{briefing_count}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _inject_css()
    _render_header()

    tab_briefing, tab_mission = st.tabs(["📋  Executive Briefing", "🎯  Mission Control"])

    with tab_briefing:
        _render_briefing_tab()

    with tab_mission:
        _render_mission_tab()


if __name__ == "__main__":
    main()
else:
    main()
