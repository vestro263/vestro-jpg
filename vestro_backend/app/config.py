from pydantic_settings import BaseSettings
from functools import lru_cache
from dotenv import load_dotenv
import os

# 👇 FORCE load .env from project root
load_dotenv()

print("DEBUG DATABASE_URL =", os.getenv("DATABASE_URL"))


class Settings(BaseSettings):
    database_url: str
    crunchbase_api_key: str = ""
    news_api_key: str = ""

    # ✅ ADD THESE
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None

    cors_origins: str = "http://localhost:5173"
    port: int = 8000

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

@lru_cache
def get_settings() -> Settings:
    return Settings()