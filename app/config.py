from pydantic import BaseModel
from pydantic_settings import BaseSettings
import json, os, boto3

class Settings(BaseSettings):
    APP_NAME: str = "bab.ai API"
    STAGE: str = "prod"
    # Either provide DATABASE_URL directly, or provide SECRET_NAME to fetch creds
    DATABASE_URL: str | None = None
    SECRET_NAME: str | None = None
    AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")
    CORS_ORIGINS: list[str] = []

    class Config:
        env_file = ".env"
        case_sensitive = True

def get_db_url(settings: Settings) -> str:
    if settings.DATABASE_URL:
        return settings.DATABASE_URL
    if not settings.SECRET_NAME:
        raise RuntimeError("No DATABASE_URL or SECRET_NAME provided.")

    sm = boto3.client("secretsmanager", region_name=settings.AWS_REGION)
    secret = sm.get_secret_value(SecretId=settings.SECRET_NAME)["SecretString"]
    s = json.loads(secret)  # expected keys: host, port, username, password, dbname
    return (
        f"postgresql+asyncpg://{s['username']}:{s['password']}"
        f"@{s['host']}:{s.get('port',5432)}/{s['dbname']}"
    )
