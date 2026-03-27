"""
LinkedIn headcount scraper — Playwright (headless Chromium).
Extracts employee count from company pages.
Saves only the % delta — not the raw count — to keep data lean.
Runs slowly by design (REQUEST_DELAY) to avoid LinkedIn rate limits.
"""
import asyncio
import re
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Firm, Signal
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

REQUEST_DELAY = 5   # seconds between firms
MAX_FIRMS_PER_RUN = 50  # cap per scheduler run to stay under rate limits


def _parse_count(text: str) -> int | None:
    t = text.lower().replace(",", "").replace(".", "")
    # "1.2k followers" or "1200 employees"
    m = re.search(r"([\d]+)\s*k", t)
    if m:
        return int(m.group(1)) * 1000
    m = re.search(r"(\d+)\+?\s*(employee|follower|member)", t)
    if m:
        return int(m.group(1))
    return None


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=5, max=15))
async def _scrape(domain: str, page) -> int | None:
    # Try slug derived from domain first, then fall back to search
    slug = domain.split(".")[0]
    url  = f"https://www.linkedin.com/company/{slug}/"

    try:
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Several selectors LinkedIn has used historically
        selectors = [
            "[data-anonymize='headcount']",
            ".org-top-card-summary-info-list__info-item",
            "dd.org-about-company-module__company-staff-count-range",
            ".t-black--light.t-14",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    txt = await el.inner_text()
                    count = _parse_count(txt)
                    if count:
                        return count
            except Exception:
                continue

        # Fallback: regex scan page source
        html = await page.content()
        m = re.search(r"([\d,]+)\s*employees", html, re.IGNORECASE)
        if m:
            return _parse_count(m.group(0))
    except Exception as e:
        log.debug(f"LinkedIn scrape error for {domain}: {e}")
    return None


async def run(db: AsyncSession):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("Playwright missing. Run: pip install playwright && playwright install chromium")
        return

    result = await db.execute(
        select(Firm).where(Firm.domain.isnot(None)).limit(MAX_FIRMS_PER_RUN)
    )
    firms = result.scalars().all()
    if not firms:
        log.info("LinkedIn: no firms to scrape")
        return

    log.info(f"LinkedIn scraper starting — {len(firms)} firms")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
        )
        # Block images + fonts — speed up page loads
        await ctx.route("**/*.{png,jpg,gif,svg,woff,woff2,ttf,otf}",
                        lambda r: r.abort())

        page = await ctx.new_page()

        for firm in firms:
            try:
                new_count = await _scrape(firm.domain, page)
                if new_count is None:
                    continue

                if firm.employee_count and firm.employee_count > 0:
                    delta = (new_count - firm.employee_count) / firm.employee_count
                    if abs(delta) > 0.02:          # ignore noise <2%
                        db.add(Signal(
                            firm_id=firm.id,
                            type="headcount_delta",
                            value=round(delta, 4),
                            source="linkedin",
                        ))
                        log.info(f"LinkedIn: {firm.name} Δ{delta:+.1%}")

                firm.employee_count = new_count
                await db.commit()

            except Exception as e:
                log.error(f"LinkedIn: {firm.name} — {e}")
                await db.rollback()

            await asyncio.sleep(REQUEST_DELAY)

        await browser.close()

    log.info("LinkedIn scraper complete")