"""Runtime settings (environment variables only - no secrets in code or Git)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List

APP_VERSION = "1.0.0"
API_VERSION = "v1"
MODEL_VERSION = "bf-physics-1.0.0"


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> List[str]:
    raw = os.environ.get(name, default)
    return [x.strip().rstrip("/") for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", True))
    deployment_tier: str = field(default_factory=lambda: os.environ.get("DEPLOYMENT_TIER", "cloud"))
    allow_plant_writes: bool = field(default_factory=lambda: _bool("ALLOW_PLANT_WRITES", False))
    cors_origins: List[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "./data")))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", ""))
    jwt_expire_min: int = field(default_factory=lambda: int(os.environ.get("JWT_EXPIRE_MIN", "480")))
    demo_user_password: str = field(default_factory=lambda: os.environ.get("DEMO_USER_PASSWORD", ""))
    allow_anonymous_viewer: bool = field(default_factory=lambda: _bool("ALLOW_ANONYMOUS_VIEWER", True))
    plant_preset: str = field(default_factory=lambda: os.environ.get("PLANT_PRESET", "large_4000"))
    sim_speed: float = field(default_factory=lambda: float(os.environ.get("SIM_SPEED", "30")))
    sim_autostart: bool = field(default_factory=lambda: _bool("SIM_AUTOSTART", True))
    rate_limit_per_min: int = field(default_factory=lambda: int(os.environ.get("RATE_LIMIT_PER_MIN", "600")))
    max_upload_mb: float = field(default_factory=lambda: float(os.environ.get("MAX_UPLOAD_MB", "10")))
    raw_sample_retention_days: float = field(default_factory=lambda: float(os.environ.get("RAW_SAMPLE_RETENTION_DAYS", "7")))
    edge_ingest_token: str = field(default_factory=lambda: os.environ.get("EDGE_INGEST_TOKEN", ""))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    worker_threads: int = field(default_factory=lambda: int(os.environ.get("WORKER_THREADS", "2")))
    nmpc_time_budget_s: float = field(default_factory=lambda: float(os.environ.get("NMPC_TIME_BUDGET_S", "45")))

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'bf_twin.db').as_posix()}"
        if self.database_url.startswith("postgres://"):  # Render-style URL
            self.database_url = "postgresql+psycopg2://" + self.database_url[len("postgres://"):]
        self.ephemeral_jwt_secret = False
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_urlsafe(48)
            self.ephemeral_jwt_secret = True

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def public_dict(self) -> dict:
        return {"app_env": self.app_env, "demo_mode": self.demo_mode, "deployment_tier": self.deployment_tier,
                "allow_plant_writes": self.allow_plant_writes, "cors_origins": self.cors_origins,
                "database": self.database_url.split(":")[0], "sim_speed": self.sim_speed,
                "anonymous_viewer": self.allow_anonymous_viewer, "ephemeral_jwt_secret": self.ephemeral_jwt_secret}


@lru_cache
def get_settings() -> Settings:
    return Settings()
