"""
Crawl Research Node (CRN) — System Prompts
===========================================
Centralised prompt templates for LLM calls with domain routing.
Output schema: structured JSON for machine-parseable intelligence reports.
"""
from app.core.targets_loader import load_targets

TARGET_URLS = load_targets()

_VALID_TAGS = '["AI", "LLM", "Data Science", "Indonesia", "Economy", "Layoffs", "Cloud", "Career", "Research"]'

_LENS_INSTRUCTIONS = {
    "executive": "Focus on high-level executive summary, top insights, and strategic impact.",
    "technical": "Focus on system architecture, tech stack, empirical benchmarks, and code implementation details.",
    "risk": "Focus on risk audit, vulnerabilities, security concerns, flaws, and counter-arguments.",
    "custom": "Focus specifically on answering this custom query: '{custom_query}'."
}

_BASE_TEMPLATE = """\
{persona}

Researcher Profile:
{researcher_profile}

Analytical Lens Focus:
{lens_instruction}

Read the following extracted web crawler text and produce a structured intelligence report.

You MUST return ONLY a single valid JSON object. No markdown fences, no preamble, no explanation outside the JSON.

The JSON object must contain exactly these fields:

{{
  "title": "<string: article headline or page title>",
  "one_line_brief": "<string: a single, sharp, 15-word plain-language summary tailored to the Analytical Lens focus above>",
  "relevance_tags": ["<from fixed set: {valid_tags}>"],
  "relevance_score": <integer 1-10, where 10 = critically relevant to the researcher profile above>,
  "global_local": "<one of: global, local, both>",
  "analytical_insight": "<string: 2-3 sentence detailed analysis specific to the Analytical Lens focus above>"
}}

RULES:
- "one_line_brief" must be at most 15 words. Write it as if sending a push notification.
- "relevance_tags" values MUST only come from this fixed set: {valid_tags}
- "relevance_score" must be an integer between 1 and 10. Use the Researcher Profile to calibrate.
- "global_local" must be exactly one of: "global", "local", "both"
- "analytical_insight" must provide substantive depth following the selected Analytical Lens.
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


def get_prompt_for_url(
    url: str,
    text: str,
    researcher_profile: str,
    lens: str = "executive",
    custom_query: str = ""
) -> str:
    """Return a domain-specific prompt by injecting persona, user profile, and selected Analytical Lens."""
    url_lower = url.lower()
    
    if any(domain in url_lower for domain in ["github.com", "techcrunch.com", "theverge.com", "stackoverflow.blog", "detik.com"]):
        persona = _PERSONAS["tech"]
    elif any(domain in url_lower for domain in ["finance.yahoo.com", "technologyreview.com", "wsj.com", "cnbcindonesia.com"]):
        persona = _PERSONAS["finance"]
    elif any(domain in url_lower for domain in ["arxiv.org", "huggingface.co", "paperswithcode.com"]):
        persona = _PERSONAS["ai_research"]
    elif any(domain in url_lower for domain in ["substack.com", "e27.co", "dailysocial.id"]):
        persona = _PERSONAS["newsletter"]
    else:
        persona = _PERSONAS["default"]

    raw_lens = _LENS_INSTRUCTIONS.get(lens, _LENS_INSTRUCTIONS["executive"])
    if lens == "custom" and custom_query:
        lens_instruction = raw_lens.format(custom_query=custom_query)
    else:
        lens_instruction = raw_lens

    return _BASE_TEMPLATE.format(
        persona=persona, 
        researcher_profile=researcher_profile,
        lens_instruction=lens_instruction,
        text=text, 
        valid_tags=_VALID_TAGS
    )
