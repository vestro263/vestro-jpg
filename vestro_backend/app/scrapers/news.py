"""
News scraper — three sources:
  1. GDELT API        (global, free, no key)
  2. Google News RSS  (per-firm search)
  3. TechCrunch + Sifted RSS (startup-focused bulk fetch)

Saves a news_sentiment Signal per relevant article.
Sentiment: simple keyword model (-1.0 → +1.0).
Swap simple_sentiment() for finBERT once you have GPU on Render.
"""
import httpx
import feedparser
import logging
import asyncio
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Firm, Signal

log = logging.getLogger(__name__)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
RSS_FEEDS  = [
    "https://techcrunch.com/feed/",
    "https://sifted.eu/feed/",
]

POSITIVE = {
    "raises", "funding", "growth", "expands", "launches", "acquires",
    "record", "profit", "revenue", "partnership", "wins", "breakthrough",
    "hires", "promotes", "series", "unicorn", "scale", "ipo", "valued",
}
NEGATIVE = {
    "layoffs", "cuts", "loss", "bankrupt", "fraud", "lawsuit", "restructure",
    "decline", "shutdown", "crisis", "debt", "miss", "resign", "fired",
    "investigation", "breach", "penalty", "default", "collapse",
}


def _sentiment(text: str) -> float:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    pos   = len(words & POSITIVE)
    neg   = len(words & NEGATIVE)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)


async def _gdelt(name: str, client: httpx.AsyncClient) -> list[str]:
    try:
        r = await client.get(GDELT_URL, params={
            "query": f'"{name}" sourcelang:english',
            "mode": "artlist", "maxrecords": 8,
            "format": "json", "timespan": "3d",
        }, timeout=15)
        if r.status_code != 200:
            return []
        return [a.get("title", "") for a in r.json().get("articles", [])]
    except Exception as e:
        log.debug(f"GDELT {name}: {e}")
        return []


async def _google_news(name: str, client: httpx.AsyncClient) -> list[str]:
    query = name.replace(" ", "+")
    url   = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
    try:
        r    = await client.get(url, timeout=15)
        feed = feedparser.parse(r.text)
        return [e.title for e in feed.entries[:8]]
    except Exception as e:
        log.debug(f"Google News {name}: {e}")
        return []


def _bulk_rss() -> list[dict]:
    """Fetch TechCrunch + Sifted once per run."""
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:25]:
                articles.append({
                    "title":   e.get("title", ""),
                    "summary": e.get("summary", ""),
                    "source":  "rss",
                })
        except Exception as e:
            log.warning(f"RSS {url}: {e}")
    return articles


def _rss_matches(articles: list[dict], firms: list[Firm]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in articles:
        blob = (a["title"] + " " + a["summary"]).lower()
        for firm in firms:
            if firm.name.lower() in blob:
                title = a["title"]
                out.setdefault(firm.id, []).append(title)
    return out


async def run(db: AsyncSession):
    result = await db.execute(select(Firm))
    firms  = result.scalars().all()
    if not firms:
        return

    log.info(f"News scraper starting — {len(firms)} firms")

    rss_articles = _bulk_rss()
    rss_map      = _rss_matches(rss_articles, firms)

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; Vestro/1.0)"},
        follow_redirects=True,
    ) as client:
        for firm in firms:
            added = 0
            titles = set()   # dedupe within this run

            for title in await _gdelt(firm.name, client):
                if title and title not in titles:
                    s = _sentiment(title)
                    if s != 0:
                        db.add(Signal(firm_id=firm.id, type="news_sentiment",
                                      value=s, text=title[:500], source="gdelt"))
                        added += 1
                    titles.add(title)

            for title in await _google_news(firm.name, client):
                if title and title not in titles:
                    s = _sentiment(title)
                    if s != 0:
                        db.add(Signal(firm_id=firm.id, type="news_sentiment",
                                      value=s, text=title[:500], source="google_news"))
                        added += 1
                    titles.add(title)

            for title in rss_map.get(firm.id, []):
                if title not in titles:
                    s = _sentiment(title)
                    if s != 0:
                        db.add(Signal(firm_id=firm.id, type="news_sentiment",
                                      value=s, text=title[:500], source="rss"))
                        added += 1
                    titles.add(title)

            if added:
                await db.commit()
                log.info(f"News: {firm.name} → {added} signals")

            await asyncio.sleep(0.4)

    log.info("News scraper complete")