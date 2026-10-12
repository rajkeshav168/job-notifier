"""
fetchers.py — Fetch job listings from various sources.

Each fetcher returns a list of dicts with a standard schema:
    {
        "id":       str,   # unique identifier (ATS job ID or URL hash)
        "title":    str,   # job title
        "location": str,   # location or "Remote"
        "url":      str,   # direct link to the posting
        "company":  str,   # company name (passed through)
    }

WHY THIS DESIGN:
- Normalize different ATS responses into one shape → makes diffing trivial
- Each fetcher is independent → easy to add new ATS platforms
- Stateless → fetchers just grab current state, diffing happens elsewhere
"""

import hashlib
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Timeout for all HTTP requests (connect, read) in seconds
REQUEST_TIMEOUT = 15


# ─── Greenhouse ─────────────────────────────────────────────
# Docs: https://developers.greenhouse.io/job-board.html
# Public, no auth needed. Returns paginated JSON.

def fetch_greenhouse(company_id: str, company_name: str) -> list[dict]:
    """
    Greenhouse boards API.
    URL pattern: https://boards-api.greenhouse.io/v1/boards/{id}/jobs
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs"
    params = {"content": "false"}  # skip full job descriptions to save bandwidth

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[Greenhouse] Failed to fetch {company_name}: {e}")
        return []

    data = resp.json()
    jobs = []

    for job in data.get("jobs", []):
        location = job.get("location", {}).get("name", "Unknown")
        jobs.append({
            "id": str(job["id"]),
            "title": job["title"],
            "location": location,
            "url": job.get("absolute_url", f"https://boards.greenhouse.io/{company_id}/jobs/{job['id']}"),
            "company": company_name,
        })

    logger.info(f"[Greenhouse] {company_name}: fetched {len(jobs)} listings")
    return jobs


# ─── Lever ──────────────────────────────────────────────────
# Docs: https://github.com/lever/postings-api
# Public, no auth needed.

def fetch_lever(company_id: str, company_name: str) -> list[dict]:
    """
    Lever postings API.
    URL pattern: https://api.lever.co/v0/postings/{id}?mode=json
    """
    url = f"https://api.lever.co/v0/postings/{company_id}"
    params = {"mode": "json"}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[Lever] Failed to fetch {company_name}: {e}")
        return []

    data = resp.json()
    jobs = []

    for job in data:
        location = job.get("categories", {}).get("location", "Unknown")
        jobs.append({
            "id": job["id"],
            "title": job["text"],
            "location": location,
            "url": job.get("hostedUrl", f"https://jobs.lever.co/{company_id}/{job['id']}"),
            "company": company_name,
        })

    logger.info(f"[Lever] {company_name}: fetched {len(jobs)} listings")
    return jobs


# ─── Ashby ──────────────────────────────────────────────────
# Ashby uses a POST endpoint that returns JSON.

def fetch_ashby(company_id: str, company_name: str) -> list[dict]:
    """
    Ashby posting API.
    POST to: https://api.ashbyhq.com/posting-api/job-board/{id}
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_id}"

    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[Ashby] Failed to fetch {company_name}: {e}")
        return []

    data = resp.json()
    jobs = []

    for job in data.get("jobs", []):
        location = job.get("location", "Unknown")
        job_id = job.get("id", hashlib.md5(job["title"].encode()).hexdigest())
        jobs.append({
            "id": str(job_id),
            "title": job["title"],
            "location": location,
            "url": job.get("jobUrl", f"https://jobs.ashbyhq.com/{company_id}/{job_id}"),
            "company": company_name,
        })

    logger.info(f"[Ashby] {company_name}: fetched {len(jobs)} listings")
    return jobs


# ─── Generic Scraper (fallback) ─────────────────────────────
# For companies without a known ATS API.
# Requires CSS selectors in the config.

def fetch_scrape(url: str, company_name: str, selector: str, link_selector: str = None) -> list[dict]:
    """
    Generic HTML scraper. Grabs elements matching `selector` as job titles,
    and optionally `link_selector` for the URLs.
    """
    try:
        headers = {"User-Agent": "JobNotifier/1.0 (personal project)"}
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[Scrape] Failed to fetch {company_name}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    title_elements = soup.select(selector)

    # If a link selector is given, grab those too
    link_elements = soup.select(link_selector) if link_selector else []

    jobs = []
    for i, el in enumerate(title_elements):
        title = el.get_text(strip=True)
        # Try to get the link from link_elements or from the title element itself
        job_url = ""
        if i < len(link_elements):
            job_url = link_elements[i].get("href", "")
        elif el.name == "a":
            job_url = el.get("href", "")

        # Make relative URLs absolute
        if job_url and not job_url.startswith("http"):
            from urllib.parse import urljoin
            job_url = urljoin(url, job_url)

        job_id = hashlib.md5(f"{title}:{job_url}".encode()).hexdigest()

        jobs.append({
            "id": job_id,
            "title": title,
            "location": "See posting",
            "url": job_url or url,
            "company": company_name,
        })

    logger.info(f"[Scrape] {company_name}: found {len(jobs)} elements")
    return jobs


# ─── Greenhouse EU ──────────────────────────────────────────
# Some companies (e.g. Groww) use the EU instance of Greenhouse.
# Different base URL, same JSON structure.

def fetch_greenhouse_eu(company_id: str, company_name: str) -> list[dict]:
    """
    Greenhouse EU boards API.
    URL: https://boards-api.eu.greenhouse.io/v1/boards/{id}/jobs
    """
    url = f"https://boards-api.eu.greenhouse.io/v1/boards/{company_id}/jobs"
    params = {"content": "false"}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[Greenhouse EU] Failed to fetch {company_name}: {e}")
        return []

    data = resp.json()
    jobs = []

    for job in data.get("jobs", []):
        location = job.get("location", {}).get("name", "Unknown")
        jobs.append({
            "id": str(job["id"]),
            "title": job["title"],
            "location": location,
            "url": job.get("absolute_url", f"https://job-boards.eu.greenhouse.io/{company_id}/jobs/{job['id']}"),
            "company": company_name,
        })

    logger.info(f"[Greenhouse EU] {company_name}: fetched {len(jobs)} listings")
    return jobs


# ─── SmartRecruiters ────────────────────────────────────────
# Docs: https://dev.smartrecruiters.com/customer-api/
# Public posting API — no auth needed.
# Used by: Zomato, Visa, Bosch, IKEA, etc.

def fetch_smartrecruiters(company_id: str, company_name: str) -> list[dict]:
    """
    SmartRecruiters posting API.
    URL: https://api.smartrecruiters.com/v1/companies/{id}/postings
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
    params = {"limit": 100}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[SmartRecruiters] Failed to fetch {company_name}: {e}")
        return []

    data = resp.json()
    jobs = []

    for job in data.get("content", []):
        location_obj = job.get("location", {})
        location = location_obj.get("city", location_obj.get("country", "Unknown"))

        jobs.append({
            "id": job["id"],
            "title": job["name"],
            "location": location,
            "url": job.get("ref", f"https://careers.smartrecruiters.com/{company_id}/{job['id']}"),
            "company": company_name,
        })

    logger.info(f"[SmartRecruiters] {company_name}: fetched {len(jobs)} listings")
    return jobs


# ─── Playwright (for JS-rendered career pages) ─────────────
# Companies like Amazon, Google, Microsoft render job listings
# via JavaScript. requests+BeautifulSoup can't see them.
# Playwright launches a real browser, waits for JS to finish,
# then extracts the rendered HTML.
#
# TWO EXTRACTION MODES:
#
# 1. FLAT MODE (simple): selector grabs titles, link_selector grabs links,
#    zip by index. Works when lists are guaranteed 1:1.
#    e.g. Amazon's ".job-title a" gives both title and link.
#
# 2. CARD MODE (robust): card_selector finds each job card container,
#    then selector + link_selector extract title/link WITHIN each card.
#    No index mismatch possible. Use when title and link are siblings
#    inside a wrapper (Google, Goldman Sachs, Walmart).
#
#    For sites with no <a> links (like Walmart's div[role="link"]),
#    use url_attribute to read a data attribute from the card, and
#    url_template to construct the URL:
#      url_attribute: "data-job-id"
#      url_template: "https://careers.walmart.com/us/jobs/{}"

def fetch_playwright(url: str, company_name: str, selector: str,
                     link_selector: str = None, wait_selector: str = None,
                     card_selector: str = None, url_attribute: str = None,
                     url_template: str = None) -> list[dict]:
    """
    Playwright-based scraper for JS-rendered career pages.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[Playwright] playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    wait_for = wait_selector or card_selector or selector
    jobs = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            logger.info(f"[Playwright] Loading {company_name}...")
            page.goto(url, timeout=30000)

            # Wait for job listings to render
            try:
                page.wait_for_selector(wait_for, timeout=15000)
            except Exception:
                logger.warning(f"[Playwright] {company_name}: wait selector '{wait_for}' not found, trying page content anyway")

            # Give extra time for lazy-loaded content
            page.wait_for_timeout(2000)

            if card_selector:
                # ── CARD MODE: extract title+link from within each card ──
                cards = page.query_selector_all(card_selector)

                for card in cards:
                    # Extract title
                    title_el = card.query_selector(selector)
                    if not title_el:
                        continue
                    title = title_el.inner_text().strip()
                    if not title:
                        continue

                    # Extract link
                    job_url = ""
                    if url_attribute:
                        # Read a data attribute from the card itself (e.g. Walmart's data-job-id)
                        attr_val = card.get_attribute(url_attribute) or ""
                        if attr_val and url_template:
                            job_url = url_template.format(attr_val)
                    elif link_selector:
                        link_el = card.query_selector(link_selector)
                        if link_el:
                            job_url = link_el.get_attribute("href") or ""
                    else:
                        # Try to find any <a> in the card
                        try:
                            job_url = card.evaluate("el => { const a = el.querySelector('a'); return a ? a.href : ''; }")
                        except Exception:
                            pass

                    # Make relative URLs absolute
                    if job_url and not job_url.startswith("http"):
                        from urllib.parse import urljoin
                        job_url = urljoin(url, job_url)

                    # job_id = hashlib.md5(f"{company_name}:{title}:{job_url}".encode()).hexdigest()
                    job_id = hashlib.md5(f"{company_name}:{title}".encode()).hexdigest()

                    jobs.append({
                        "id": job_id,
                        "title": title,
                        "location": "See posting",
                        "url": job_url or url,
                        "company": company_name,
                    })
            else:
                # ── FLAT MODE: separate title and link lists ──
                title_elements = page.query_selector_all(selector)

                link_elements = []
                if link_selector:
                    link_elements = page.query_selector_all(link_selector)

                for i, el in enumerate(title_elements):
                    title = el.inner_text().strip()
                    if not title:
                        continue

                    job_url = ""
                    if i < len(link_elements):
                        job_url = link_elements[i].get_attribute("href") or ""
                    else:
                        # Try to find a link: child <a> first, then parent <a>
                        try:
                            child_link = el.query_selector("a")
                            if child_link:
                                job_url = child_link.get_attribute("href") or ""
                            else:
                                job_url = el.evaluate("el => { const a = el.closest('a'); return a ? a.href : ''; }")
                        except Exception:
                            pass

                    if job_url and not job_url.startswith("http"):
                        from urllib.parse import urljoin
                        job_url = urljoin(url, job_url)

                    # job_id = hashlib.md5(f"{company_name}:{title}:{job_url}".encode()).hexdigest()
                    job_id = hashlib.md5(f"{company_name}:{title}".encode()).hexdigest()

                    jobs.append({
                        "id": job_id,
                        "title": title,
                        "location": "See posting",
                        "url": job_url or url,
                        "company": company_name,
                    })

            browser.close()

    except Exception as e:
        logger.error(f"[Playwright] Failed to fetch {company_name}: {e}")

    logger.info(f"[Playwright] {company_name}: found {len(jobs)} elements")
    return jobs


# ─── Dispatcher ─────────────────────────────────────────────
# Maps config `type` to the right fetcher.

def fetch_jobs(company_config: dict) -> list[dict]:
    """
    Route to the correct fetcher based on company type.
    This is the only function the orchestrator calls.
    """
    ctype = company_config["type"]
    name = company_config["name"]

    if ctype == "greenhouse":
        return fetch_greenhouse(company_config["id"], name)
    elif ctype == "greenhouse_eu":
        return fetch_greenhouse_eu(company_config["id"], name)
    elif ctype == "lever":
        return fetch_lever(company_config["id"], name)
    elif ctype == "ashby":
        return fetch_ashby(company_config["id"], name)
    elif ctype == "smartrecruiters":
        return fetch_smartrecruiters(company_config["id"], name)
    elif ctype == "scrape":
        return fetch_scrape(
            url=company_config["url"],
            company_name=name,
            selector=company_config["selector"],
            link_selector=company_config.get("link_selector"),
        )
    elif ctype == "playwright":
        return fetch_playwright(
            url=company_config["url"],
            company_name=name,
            selector=company_config["selector"],
            link_selector=company_config.get("link_selector"),
            wait_selector=company_config.get("wait_selector"),
            card_selector=company_config.get("card_selector"),
            url_attribute=company_config.get("url_attribute"),
            url_template=company_config.get("url_template"),
        )
    else:
        logger.error(f"Unknown company type: {ctype} for {name}")
        return []