"""
CRN Job Intelligence Scraper Engine
===================================
Uses Async Playwright + HTTP JSON APIs to scrape AI Engineer, MLOps, and Data Science
job listings from global remote and SEA job boards.
"""

import asyncio
import logging
import re
import requests
from pathlib import Path
from playwright.async_api import async_playwright
import trafilatura

from app.agent.job_logic import evaluate_job_fit, evaluate_job_batch

logger = logging.getLogger(__name__)

# Search Target Keywords
TARGET_ROLES = ["AI Engineer", "MLOps Engineer", "Data Engineer", "LLM Engineer", "Machine Learning Engineer"]


async def fetch_remote_jobs_api() -> list[dict]:
    """
    Fetch live remote AI/ML/Data jobs from public RemoteOK and Remotive APIs.
    No API key required.
    """
    jobs = []
    
    # 1. Fetch RemoteOK API
    try:
        url = "https://remoteok.com/api"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=10)
        if resp.ok:
            data = resp.json()
            # RemoteOK data[0] is legal header, rest are job items
            for item in data[1:15]:
                title = item.get("position", "")
                tags = [t.lower() for t in item.get("tags", [])]
                # Filter for AI/ML/Data/Python roles
                if any(k.lower() in title.lower() or k.lower() in tags for k in ["ai", "machine learning", "data", "python", "mlops", "nlp"]):
                    jobs.append({
                        "title": title,
                        "company": item.get("company", "Remote Tech"),
                        "location": "Global Remote",
                        "url": item.get("url") or f"https://remoteok.com/remote-jobs/{item.get('id')}",
                        "source_board": "RemoteOK",
                        "description": item.get("description", title)
                    })
    except Exception as exc:
        logger.warning("RemoteOK API fetch failed: %s", exc)

    # 2. Fetch Remotive API
    try:
        remotive_url = "https://remotive.com/api/remote-jobs?category=software-dev&limit=20"
        resp = requests.get(remotive_url, timeout=10)
        if resp.ok:
            data = resp.json().get("jobs", [])
            for item in data[:15]:
                title = item.get("title", "")
                if any(k.lower() in title.lower() for k in ["ai", "machine learning", "data", "mlops", "python"]):
                    jobs.append({
                        "title": title,
                        "company": item.get("company_name", "Tech Startup"),
                        "location": item.get("candidate_required_location", "Remote"),
                        "url": item.get("url"),
                        "source_board": "Remotive",
                        "description": trafilatura.extract(item.get("description", "")) or title
                    })
    except Exception as exc:
        logger.warning("Remotive API fetch failed: %s", exc)

    return jobs


async def fetch_local_indonesia_jobs() -> list[dict]:
    """
    Fetch live local Indonesian AI, MLOps, and Data Science job listings from JobStreet Indonesia
    and LinkedIn Indonesia Guest Search using Playwright.
    """
    local_jobs = []
    logger.info("Scraping local Indonesian job postings from JobStreet & LinkedIn Indonesia...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            
            # 1. JobStreet Indonesia
            try:
                page1 = await browser.new_page()
                url1 = "https://id.jobstreet.com/id/AI-Engineer-jobs"
                await page1.goto(url1, timeout=25000, wait_until="domcontentloaded")
                
                cards1 = await page1.query_selector_all("article")
                for card in cards1[:4]:
                    links = await card.query_selector_all("a[href*='/job/']")
                    title = ""
                    href = ""
                    for link in links:
                        t = (await link.inner_text()).strip()
                        if len(t) > 3 and not title:
                            title = t
                            href = await link.get_attribute("href")
                    
                    if not title:
                        continue

                    company_elem = await card.query_selector("a[data-automation='jobCompany'], span[data-automation='jobCompany']")
                    company = (await company_elem.inner_text()).strip() if company_elem else "Indonesian Tech Firm"
                    
                    loc_elem = await card.query_selector("span[data-automation='jobLocation']")
                    location = (await loc_elem.inner_text()).strip() if loc_elem else "Indonesia"
                    
                    full_url = f"https://id.jobstreet.com{href}" if href and href.startswith("/") else href
                    
                    local_jobs.append({
                        "title": title,
                        "company": company,
                        "location": f"🇮🇩 {location}",
                        "url": full_url,
                        "source_board": "JobStreet Indonesia",
                        "description": f"Local Indonesian position for {title} at {company} ({location})."
                    })
            except Exception as exc1:
                logger.warning("JobStreet scrape step failed: %s", exc1)

            # 2. LinkedIn Indonesia Guest Search
            try:
                page2 = await browser.new_page()
                url2 = "https://www.linkedin.com/jobs/search/?keywords=AI%20Engineer&location=Indonesia"
                await page2.goto(url2, timeout=25000, wait_until="domcontentloaded")
                
                cards2 = await page2.query_selector_all("div.job-search-card, ul.jobs-search__results-list li")
                seen_urls = set()
                for card in cards2[:6]:
                    title_elem = await card.query_selector("h3.base-search-card__title, a.base-card__full-link")
                    company_elem = await card.query_selector("h4.base-search-card__subtitle, a.hidden-nested-link")
                    loc_elem = await card.query_selector("span.job-search-card__location")
                    link_elem = await card.query_selector("a.base-card__full-link")
                    
                    title = (await title_elem.inner_text()).strip() if title_elem else ""
                    company = (await company_elem.inner_text()).strip() if company_elem else "Tech Firm"
                    location = (await loc_elem.inner_text()).strip() if loc_elem else "Indonesia"
                    full_url = await link_elem.get_attribute("href") if link_elem else ""
                    
                    if title and full_url and full_url not in seen_urls:
                        seen_urls.add(full_url)
                        local_jobs.append({
                            "title": title,
                            "company": company,
                            "location": f"🇮🇩 {location}",
                            "url": full_url,
                            "source_board": "LinkedIn Indonesia",
                            "description": f"LinkedIn Indonesia position for {title} at {company} ({location})."
                        })
            except Exception as exc2:
                logger.warning("LinkedIn scrape step failed: %s", exc2)

            # 3. Kalibrr Indonesia
            try:
                page3 = await browser.new_page()
                url3 = "https://www.kalibrr.com/job-board/te/AI-Engineer/co/Indonesia/1"
                await page3.goto(url3, timeout=25000, wait_until="domcontentloaded")
                await page3.wait_for_timeout(2000)
                
                cards3 = await page3.query_selector_all("a[href*='/jobs/']")
                seen_kalibrr = set()
                for card in cards3:
                    title = (await card.inner_text()).strip()
                    href = await card.get_attribute("href")
                    if href and href not in seen_kalibrr and len(title) > 3 and title.lower() != "view post":
                        seen_kalibrr.add(href)
                        match = re.search(r'/c/([^/]+)/jobs/', href)
                        company = match.group(1).replace('-', ' ').title() if match else "Kalibrr Tech Firm"
                        full_url = f"https://www.kalibrr.com{href}" if href.startswith("/") else href
                        
                        local_jobs.append({
                            "title": title,
                            "company": company,
                            "location": "🇮🇩 Indonesia",
                            "url": full_url,
                            "source_board": "Kalibrr Indonesia",
                            "description": f"Kalibrr Indonesia position for {title} at {company}."
                        })
                        if len([j for j in local_jobs if j["source_board"] == "Kalibrr Indonesia"]) >= 4:
                            break
            except Exception as exc3:
                logger.warning("Kalibrr scrape step failed: %s", exc3)

            await browser.close()
    except Exception as exc:
        logger.warning("Local Indonesia Playwright scrape failed: %s", exc)

    return local_jobs


async def scrape_custom_job_url(job_url: str) -> dict:
    """Scrape a specific job posting URL provided by the user using Stealth Playwright & Trafilatura."""
    logger.info("Scraping custom job URL with Stealth Playwright: %s", job_url)
    description = ""
    title = "Job Position"
    company = "Tech Company"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="en-US"
            )
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

            await page.goto(job_url, timeout=12000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1000)
            
            raw_html = await page.content()
            page_title = await page.title()
            await browser.close()

            description = trafilatura.extract(raw_html) or page_title
            
            # Clean page title for Company & Title
            if " Jobs at " in page_title:
                parts = page_title.split(" Jobs at ")
                title = parts[0].strip()
                company = parts[1].split(",")[0].strip()
            elif " at " in page_title:
                parts = page_title.split(" at ")
                title = parts[0].strip()
                company = parts[1].split("|")[0].split("-")[0].strip()
            elif " - " in page_title:
                parts = page_title.split(" - ")
                title = parts[0].strip()
                company = parts[1].strip()
            else:
                title = page_title[:40]
    except Exception as exc:
        logger.warning("Stealth Playwright custom job scrape failed: %s. Falling back to HTTP.", exc)
        try:
            resp = requests.get(job_url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            description = trafilatura.extract(resp.text) or "Job description extracted via HTTP"
        except Exception:
            description = f"Job listing at {job_url}"

    # Evaluate fit
    return evaluate_job_fit(title, company, "Indonesia / SEA", description or "Job Posting Details", job_url, "Custom URL")


async def run_autonomous_job_scan() -> list[dict]:
    """
    Hybrid Batch Mode: Scrapes live jobs from JobStreet Indonesia (Local) & RemoteOK/Remotive (Global Remote),
    evaluates fit score in 4-job batches for fast 15-second execution.
    """
    remote_jobs = await fetch_remote_jobs_api()
    local_jobs = await fetch_local_indonesia_jobs()
    
    # Interleave local jobs and remote jobs
    raw_jobs = local_jobs[:4] + remote_jobs[:4]
    logger.info("Fetched %d local Indonesian + %d global remote job postings.", len(local_jobs), len(remote_jobs))

    evaluated_jobs = []
    # Batch evaluate 4 jobs per LLM call
    for i in range(0, len(raw_jobs), 4):
        chunk = raw_jobs[i:i+4]
        try:
            eval_res_chunk = evaluate_job_batch(chunk)
            evaluated_jobs.extend(eval_res_chunk)
            await asyncio.sleep(1.0)
        except Exception as exc:
            logger.error("Failed to evaluate job batch: %s", exc)

    evaluated_jobs.sort(key=lambda x: x["fit_score"], reverse=True)
    try:
        from app.agent.job_logic import sync_jobs_to_obsidian
        sync_jobs_to_obsidian()
    except Exception as exc:
        logger.warning("Failed to auto-sync jobs to Obsidian: %s", exc)
    return evaluated_jobs
