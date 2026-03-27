import asyncio
from app.db import init_db, AsyncSessionLocal
from app.models import Firm
from dotenv import load_dotenv
load_dotenv()

FIRMS = [
    {"name": "Stripe",      "domain": "stripe.com",      "sector": "Fintech",   "country": "USA",    "stage": "growth",   "employee_count": 8000,  "total_funding_usd": 8700000000},
    {"name": "Revolut",     "domain": "revolut.com",     "sector": "Fintech",   "country": "UK",     "stage": "growth",   "employee_count": 8000,  "total_funding_usd": 1700000000},
    {"name": "Databricks",  "domain": "databricks.com",  "sector": "Deep Tech", "country": "USA",    "stage": "series_g", "employee_count": 6000,  "total_funding_usd": 3500000000},
    {"name": "Canva",       "domain": "canva.com",       "sector": "SaaS",      "country": "AUS",    "stage": "growth",   "employee_count": 4000,  "total_funding_usd": 572000000},
    {"name": "Chime",       "domain": "chime.com",       "sector": "Fintech",   "country": "USA",    "stage": "series_g", "employee_count": 1500,  "total_funding_usd": 2300000000},
    {"name": "Impossible Foods", "domain": "impossiblefoods.com", "sector": "Deep Tech", "country": "USA", "stage": "series_h", "employee_count": 700, "total_funding_usd": 2100000000},
    {"name": "Wayve",       "domain": "wayve.ai",        "sector": "Deep Tech", "country": "UK",     "stage": "series_c", "employee_count": 1000,  "total_funding_usd": 1050000000},
    {"name": "Monzo",       "domain": "monzo.com",       "sector": "Fintech",   "country": "UK",     "stage": "series_i", "employee_count": 3000,  "total_funding_usd": 930000000},
    {"name": "Deel",        "domain": "deel.com",        "sector": "SaaS",      "country": "USA",    "stage": "series_d", "employee_count": 4000,  "total_funding_usd": 679000000},
    {"name": "Klarna",      "domain": "klarna.com",      "sector": "Fintech",   "country": "Sweden", "stage": "growth",   "employee_count": 5000,  "total_funding_usd": 4500000000},
    {"name": "Groq",        "domain": "groq.com",        "sector": "AI",        "country": "USA",    "stage": "series_d", "employee_count": 500,   "total_funding_usd": 1430000000},
    {"name": "Mistral AI",  "domain": "mistral.ai",      "sector": "AI",        "country": "France", "stage": "series_b", "employee_count": 200,   "total_funding_usd": 1050000000},
    {"name": "Wiz",         "domain": "wiz.io",          "sector": "Cybersecurity","country": "USA", "stage": "series_e", "employee_count": 1800,  "total_funding_usd": 1900000000},
    {"name": "xAI",         "domain": "x.ai",            "sector": "AI",        "country": "USA",    "stage": "series_c", "employee_count": 1000,  "total_funding_usd": 6000000000},
    {"name": "Northvolt",   "domain": "northvolt.com",   "sector": "CleanTech", "country": "Sweden", "stage": "growth",   "employee_count": 5000,  "total_funding_usd": 8000000000},
]

async def seed():
    await init_db()
    async with AsyncSessionLocal() as db:
        for f in FIRMS:
            from sqlalchemy import select
            existing = await db.execute(select(Firm).where(Firm.domain == f["domain"]))
            if existing.scalar_one_or_none():
                print(f"  skip  {f['name']} (exists)")
                continue
            db.add(Firm(**f))
            print(f"  added {f['name']}")
        await db.commit()
    print(f"\nDone — {len(FIRMS)} firms seeded")

asyncio.run(seed())
