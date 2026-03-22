"""
News Filter — checks upcoming economic events and blocks trading
during high-impact news windows (Section 4.2 of the strategy).

Uses ForexFactory calendar RSS as a free, unauthenticated source.
Falls back to a static weekend/holiday block if network unavailable.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List

logger = logging.getLogger(__name__)

# Currency to symbol mapping for impact filtering
CURRENCY_SYMBOLS = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "XAUUSD"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURCHF"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY", "GBPCHF"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "AUD": ["AUDUSD", "AUDNZD", "AUDJPY"],
    "NZD": ["NZDUSD", "AUDNZD"],
    "CAD": ["USDCAD", "CADJPY"],
    "CHF": ["USDCHF", "EURCHF", "GBPCHF"],
}

# High-impact event keywords (Tier 1 — always block)
TIER1_KEYWORDS = [
    "nfp", "non-farm", "nonfarm", "fomc", "fed rate", "interest rate decision",
    "cpi", "inflation", "gdp", "ecb", "boe", "boj", "rba", "rbnz",
    "unemployment rate", "jobs", "payroll",
]

# Medium-impact keywords (Tier 2 — reduce size)
TIER2_KEYWORDS = [
    "pmi", "ism", "retail sales", "trade balance", "housing",
    "consumer confidence", "manufacturing", "services",
]


class NewsEvent:
    def __init__(self, title: str, currency: str,
                 impact: str, event_time: datetime):
        self.title      = title
        self.currency   = currency
        self.impact     = impact   # "high", "medium", "low"
        self.event_time = event_time

    def is_tier1(self) -> bool:
        t = self.title.lower()
        return self.impact == "high" or any(k in t for k in TIER1_KEYWORDS)

    def is_tier2(self) -> bool:
        t = self.title.lower()
        return self.impact == "medium" or any(k in t for k in TIER2_KEYWORDS)

    def affects_symbol(self, symbol: str) -> bool:
        sym_upper = symbol.upper()
        affected  = CURRENCY_SYMBOLS.get(self.currency.upper(), [])
        return any(s in sym_upper or sym_upper in s for s in affected)


class NewsFilter:
    """
    Checks whether trading is allowed for a given symbol right now.
    Fetches calendar data once per hour and caches it.
    """

    def __init__(self, config: dict):
        sess              = config.get("session", {})
        self.block_before = sess.get("avoid_news_minutes_before", 30)
        self.block_after  = sess.get("avoid_news_minutes_after", 15)
        self._events: List[NewsEvent] = []
        self._last_fetch: float       = 0.0
        self._cache_ttl               = 3600  # 1 hour

    def _fetch_events(self):
        """
        Attempt to fetch ForexFactory calendar.
        Falls back to empty list on failure — bot continues without filter.
        """
        try:
            import requests
            from xml.etree import ElementTree as ET

            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
            r   = requests.get(url, timeout=10)
            r.raise_for_status()
            root = ET.fromstring(r.content)

            events = []
            for item in root.findall(".//event"):
                try:
                    title    = item.findtext("title", "")
                    currency = item.findtext("country", "")
                    impact   = item.findtext("impact", "low").lower()
                    date_str = item.findtext("date", "")
                    time_str = item.findtext("time", "0:00am")

                    if not date_str:
                        continue

                    # Parse "Jan 15, 2025" + "8:30am"
                    dt_str   = f"{date_str} {time_str}"
                    try:
                        ev_time = datetime.strptime(dt_str, "%b %d, %Y %I:%M%p")
                        ev_time = ev_time.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue

                    events.append(NewsEvent(title, currency, impact, ev_time))
                except Exception:
                    continue

            self._events = events
            logger.info(f"NewsFilter: loaded {len(events)} events")

        except Exception as e:
            logger.warning(f"NewsFilter fetch failed: {e} — trading without news filter")
            self._events = []

        self._last_fetch = time.time()

    def _ensure_fresh(self):
        if time.time() - self._last_fetch > self._cache_ttl:
            self._fetch_events()

    def check(self, symbol: str) -> dict:
        """
        Returns:
          allowed: bool
          tier: int (0=clear, 1=tier1 block, 2=tier2 reduce)
          event_name: str
          minutes_to_event: float
        """
        self._ensure_fresh()
        now = datetime.now(timezone.utc)

        for event in self._events:
            if not event.affects_symbol(symbol):
                continue

            diff_min = (event.event_time - now).total_seconds() / 60

            # Within block window
            if -self.block_after <= diff_min <= self.block_before:
                tier = 1 if event.is_tier1() else 2 if event.is_tier2() else 0
                if tier > 0:
                    return {
                        "allowed":          tier == 2,   # tier1 = block, tier2 = allow w/ warning
                        "tier":             tier,
                        "event_name":       event.title,
                        "currency":         event.currency,
                        "minutes_to_event": round(diff_min, 1),
                        "reduce_size":      tier == 2,
                    }

        # Weekend check (no trading over weekend)
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return {
                "allowed":    False,
                "tier":       1,
                "event_name": "Weekend",
                "currency":   "",
                "minutes_to_event": 0,
                "reduce_size": False,
            }

        return {
            "allowed":          True,
            "tier":             0,
            "event_name":       "",
            "currency":         "",
            "minutes_to_event": None,
            "reduce_size":      False,
        }

    def get_upcoming(self, symbol: str = None, hours: int = 24) -> list:
        """Return list of upcoming Tier-1 events within the next N hours."""
        self._ensure_fresh()
        now    = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)
        result = []
        for event in self._events:
            if event.event_time < now or event.event_time > cutoff:
                continue
            if symbol and not event.affects_symbol(symbol):
                continue
            if event.is_tier1() or event.is_tier2():
                result.append({
                    "title":    event.title,
                    "currency": event.currency,
                    "impact":   event.impact,
                    "time":     event.event_time.isoformat(),
                    "tier":     1 if event.is_tier1() else 2,
                })
        return sorted(result, key=lambda x: x["time"])