from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
import json, boto3

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")
    APP_NAME: str = "bab.ai API"
    STAGE: str = "dev"

    DATABASE_URL: Optional[str] = None
    SECRET_NAME: Optional[str] = None   # if using AWS Secrets Manager instead of DATABASE_URL
    AWS_REGION: str = "ap-south-1"
    CORS_ORIGINS: List[str] = []

def get_db_url(settings: Settings) -> str:
    if settings.DATABASE_URL:
        return settings.DATABASE_URL
    if settings.SECRET_NAME:
        sm = boto3.client("secretsmanager", region_name=settings.AWS_REGION)
        secret = sm.get_secret_value(SecretId=settings.SECRET_NAME)["SecretString"]
        s = json.loads(secret)  # {host,port,username,password,dbname}
        return f"postgresql+asyncpg://{s['username']}:{s['password']}@{s['host']}:{s.get('port',5432)}/{s['dbname']}"
    raise RuntimeError("Set either DATABASE_URL or SECRET_NAME.")
