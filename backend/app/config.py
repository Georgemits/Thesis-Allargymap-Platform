import os
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env (gitignored) into the process environment before any
# os.getenv() calls below. Safe to call even when the file is absent.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class BaseConfig:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/allergymap")

    # Google Maps Platform Pollen API key — primary pollen data source.
    # Falls back to Open-Meteo (no key required) when unset. Never hard-code
    # this value; it must come from backend/.env or the environment.
    GOOGLE_POLLEN_API_KEY = os.getenv("GOOGLE_POLLEN_API_KEY", "")


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
