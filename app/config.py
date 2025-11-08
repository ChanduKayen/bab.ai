# app/config.py
from __future__ import annotations

import json
import os
from typing import List, Optional

import boto3
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import quote_plus


class Settings(BaseSettings):
    # ---- Pydantic v2 settings ----
    model_config = SettingsConfigDict( 
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",          # <— accept extra env vars without error
    )

    # ---- App basics ----
    APP_NAME: str = "Thirtee  API"
    STAGE: str = "prod"

    # ---- Database ----
    DATABASE_URL: Optional[str] = None
    SECRET_NAME: Optional[str] = None           # if set, fetch creds from AWS SM
    AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")

    # ---- Optional envs used elsewhere (declare to avoid 'extra' errors) ----
    WHATSAPP_VERIFY_TOKEN: str = "babai"
    WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    VENDOR_QUOTE_URL_BASE: Optional[str] = None
    QUOTE_SUMMARY_URL: Optional[str] = None

    # ---- CORS ----
    CORS_ORIGINS: List[str] = Field(default_factory=list)

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        """
        Accept either JSON (e.g. '["https://a","https://b"]')
        or comma-separated string (e.g. 'https://a, https://b')
        """
        if not v:
            return []
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


def get_db_url(settings: Settings) -> str:
    """Return an asyncpg SQLAlchemy URL. If SECRET_NAME is set, read from AWS SM."""
    if settings.DATABASE_URL:
        return settings.DATABASE_URL

    if not settings.SECRET_NAME:
        raise RuntimeError("No DATABASE_URL or SECRET_NAME provided.")

    sm = boto3.client("secretsmanager", region_name=settings.AWS_REGION)
    secret = sm.get_secret_value(SecretId=settings.SECRET_NAME)["SecretString"]
    s = json.loads(secret)  # expected keys: host, port, username, password, dbname

    user = s["username"]
    pwd = quote_plus(s["password"])  # encode @ : / etc.
    host = s["host"]
    port = s.get("port", 5432)
    db = s["dbname"]

    return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}"
