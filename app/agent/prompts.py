"""
Ceros Research Node — System Prompts
========================================
Centralised prompt templates for LLM calls with domain routing.
Output schema: structured JSON for machine-parseable intelligence reports.
"""
from app.core.targets_loader import load_targets

TARGET_URLS = load_targets()

_VALID_TAGS = '["AI", "LLM", "Data Science", "Indonesia", "Economy", "Layoffs", "Cloud", "Career", "Research"]'

_BASE_TEMPLATE = """\
{persona}

Researcher Profile (use this to calibrate relevance_score):
{researcher_profile}

Read the following extracted web crawler text and produce a structured intelligence report.

You MUST return ONLY a single valid JSON object. No markdown fences, no preamble, no explanation outside the JSON.

The JSON object must contain exactly these fields:

{{
  "title": "<string: article headline or page title>",
  "summary": "<string: 3-4 sentence executive summary>",
  "key_insights": ["<string: insight 1>", "<string: insight 2>", "<string: insight 3>"],
  "relevance_tags": ["<from fixed set: {valid_tags}>"],
  "relevance_score": <integer 1-10, where 10 = critically relevant to the researcher profile above>,
  "global_local": "<one of: global, local, both>",
  "action_for_researcher": "<string: one concrete implication for a Data Science student based in Indonesia>"
}}

RULES:
- "key_insights" must have at most 3 items.
- "relevance_tags" values MUST only come from this fixed set: {valid_tags}
- "relevance_score" must be an integer between 1 and 10. Use the Researcher Profile to calibrate.
- "global_local" must be exactly one of: "global", "local", "both"
- Do NOT wrap the JSON in markdown code fences.
- Do NOT add any text before or after the JSON object.
- Do NOT make up information that is not in the source text.
- Do NOT emit <think> tags.

---
RAW TEXT:
{text}
"""

_PERSONAS = {
    "tech": "You are my Senior Engineering Lead. Extract the latest tech events or tools, skipping all ads.",
    "finance": "You are my Quantitative Analyst. Extract macro market signals, strategy insights, and economic shifts.",
    "ai_research": "You are my AI Research Scientist. Skim abstract details, identify recurring ML themes, and highlight core innovations.",
    "newsletter": "You are my Executive Assistant. Distill this long-form newsletter or article into sharp, actionable takeaways.",
    "default": "You are my personal Intelligence Analyst. Scan this text and give me a high-signal briefing."
}


def get_prompt_for_url(url: str, text: str, researcher_profile: str) -> str:
    """Return a domain-specific prompt by injecting the correct persona and user profile."""
    url_lower = url.lower()
    
    if any(domain in url_lower for domain in ["github.com", "techcrunch.com", "theverge.com", "stackoverflow.blog", "detik.com"]):
        persona = _PERSONAS["tech"]
    elif any(domain in url_lower for domain in ["finance.yahoo.com", "mckinsey.com", "wsj.com", "cnbcindonesia.com"]):
        persona = _PERSONAS["finance"]
    elif any(domain in url_lower for domain in ["arxiv.org", "huggingface.co", "paperswithcode.com"]):
        persona = _PERSONAS["ai_research"]
    elif any(domain in url_lower for domain in ["substack.com", "e27.co", "dailysocial.id"]):
        persona = _PERSONAS["newsletter"]
    else:
        persona = _PERSONAS["default"]

    return _BASE_TEMPLATE.format(
        persona=persona, 
        researcher_profile=researcher_profile,
        text=text, 
        valid_tags=_VALID_TAGS
    )
