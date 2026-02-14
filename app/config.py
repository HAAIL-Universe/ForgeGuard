"""Application configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings from environment."""

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    APP_URL: str = os.getenv("APP_URL", "http://localhost:8000")


settings = Settings()
