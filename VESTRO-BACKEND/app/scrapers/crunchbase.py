"""
Crunchbase scraper.
Uses the official v4 API if CRUNCHBASE_API_KEY is set,
otherwise falls back to the public search endpoint (rate-limited but free).
Saves only essential fields — domain is the dedup key.
"""
import httpx
import logging
import math
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Firm, Signal
from app.config import get_settings
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)
settings = get_settings()

CB_API_BASE     = "https://api.crunchbase.com/api/v4"
CB_PUBLIC_SEARCH = "https://www.crunchbase.com/v4/data/searches/organizations"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Vestro/1.0)",
    "Content-Type": "application/json",
}

# Sectors that attract hedge-fund-relevant private firm investment
SECTORS = [
    "Artificial Intelligence", "Fintech", "SaaS", "HealthTech",
    "CleanTech", "Cybersecurity", "Deep Tech", "Biotech",
    "EdTech", "PropTech",
]

STAGE_MAP = {
    "seed": "seed", "angel": "seed", "pre_seed": "seed",
    "series_a": "series_a", "series_b": "series_b",
    "series_c": "series_c", "series_d": "series_d",
    "growth_equity": "growth", "late_stage_vc": "growth",
    "corporate_round": "growth",
}

EMPLOYEE_MAP = {
    "c_00001_00010": 5,   "c_00011_00050": 30,  "c_00051_00100": 75,
    "c_00101_00250": 175, "c_00251_00500": 375,  "c_00501_01000": 750,
    "c_01001_05000": 3000,"c_05001_10000": 7500, "c_10001_max": 15000,
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _search(sector: str, client: httpx.AsyncClient) -> list[dict]:
    base_query = [
        {"type": "predicate", "field_id": "facet_ids",
         "operator_id": "includes", "values": ["company"]},
        {"type": "predicate", "field_id": "status",
         "operator_id": "includes", "values": ["operating"]},
        {"type": "predicate", "field_id": "categories",
         "operator_id": "includes", "values": [sector]},
        # Exclude public/post-IPO
        {"type": "predicate", "field_id": "last_funding_type",
         "operator_id": "not_includes",
         "values": ["post_ipo_equity", "post_ipo_debt", "ipo"]},
    ]
    payload = {
        "field_ids": [
            "identifier", "num_employees_enum", "funding_total",
            "last_funding_type", "last_funding_at", "website_url",
            "location_identifiers", "categories",
        ],
        "query": base_query,
        "order": [{"field_id": "last_funding_at", "sort": "desc"}],
        "limit": 25,
    }
    if settings.crunchbase_api_key:
        url = f"{CB_API_BASE}/searches/organizations"
        resp = await client.post(
            url,
            json=payload,
            params={"user_key": settings.crunchbase_api_key},
            headers=HEADERS,
            timeout=20,
        )
    else:
        resp = await client.post(CB_PUBLIC_SEARCH, json=payload,
                                  headers=HEADERS, timeout=20)
    if resp.status_code == 429:
        log.warning("Crunchbase rate-limited")
        return []
    resp.raise_for_status()
    return resp.json().get("entities", [])


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def upsert_firm(entity: dict, sector: str, db: AsyncSession) -> Firm | None:
    props = entity.get("properties", {})
    identifier = props.get("identifier", {})
    name = identifier.get("value", "").strip()
    if not name:
        return None

    website = props.get("website_url", "") or ""
    domain = (website.replace("https://", "").replace("http://", "")
              .split("/")[0].strip().lower())
    if not domain:
        return None

    result = await db.execute(select(Firm).where(Firm.domain == domain))
    firm = result.scalar_one_or_none()

    emp    = EMPLOYEE_MAP.get(props.get("num_employees_enum"))
    stage  = STAGE_MAP.get(props.get("last_funding_type", ""), "unknown")
    locs   = props.get("location_identifiers", [])
    country= locs[0].get("value") if locs else None

    funding_data = props.get("funding_total") or {}
    funding_usd  = float(funding_data.get("value_usd") or 0) or None
    last_funded  = _parse_date(props.get("last_funding_at"))

    if firm:
        prev_emp = firm.employee_count
        firm.stage             = stage or firm.stage
        firm.country           = country or firm.country
        firm.total_funding_usd = funding_usd or firm.total_funding_usd
        firm.last_funding_date = last_funded or firm.last_funding_date
        if emp:
            firm.employee_count = emp
        # Headcount delta signal if employee count changed meaningfully
        if prev_emp and emp and emp != prev_emp:
            delta = (emp - prev_emp) / prev_emp
            if abs(delta) > 0.02:
                db.add(Signal(firm_id=firm.id, type="headcount_delta",
                              value=round(delta, 4), source="crunchbase"))
    else:
        firm = Firm(
            name=name, domain=domain, sector=sector,
            country=country, stage=stage,
            employee_count=emp,
            total_funding_usd=funding_usd,
            last_funding_date=last_funded,
            crunchbase_url=(
                "https://www.crunchbase.com/organization/"
                + identifier.get("permalink", "")
            ),
        )
        db.add(firm)
        await db.flush()

    # Always emit a funding signal if funding data present
    if funding_usd and funding_usd > 0:
        db.add(Signal(firm_id=firm.id, type="funding_round",
                      value=funding_usd, source="crunchbase"))

    await db.commit()
    return firm


async def run(db: AsyncSession):
    log.info("Crunchbase scraper starting")
    async with httpx.AsyncClient() as client:
        for sector in SECTORS:
            try:
                entities = await _search(sector, client)
                for e in entities:
                    await upsert_firm(e, sector, db)
                log.info(f"Crunchbase [{sector}] — {len(entities)} firms processed")
            except Exception as exc:
                log.error(f"Crunchbase [{sector}] failed: {exc}")
    log.info("Crunchbase scraper done")