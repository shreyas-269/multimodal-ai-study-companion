from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_UPLOAD_SIZE_BYTES = 30 * 1024 * 1024  # 30 MB


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required API and runner secrets (no default values)
    gemini_api_key: str
    jobs_runner_secret: str

    # Required app configurations
    gemini_model: str
    firebase_project_id: str
    firebase_storage_bucket: str
    cors_origins: str

    # Optional paths and emulator configurations
    course_data_dir: str | None = None
    google_application_credentials: str | None = None
    firestore_emulator_host: str | None = None
    firebase_auth_emulator_host: str | None = None
    storage_emulator_host: str | None = None
    gemini_fallback_models: str | None = None

    @field_validator(
        "google_application_credentials",
        "firestore_emulator_host",
        "firebase_auth_emulator_host",
        "storage_emulator_host",
        "course_data_dir",
        "gemini_fallback_models",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if v == "" or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @property
    def gemini_model_chain(self) -> list[str]:
        chain = [self.gemini_model]
        if self.gemini_fallback_models:
            for raw in self.gemini_fallback_models.split(","):
                m = raw.strip()
                if m and m not in chain:
                    chain.append(m)
        return chain

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
