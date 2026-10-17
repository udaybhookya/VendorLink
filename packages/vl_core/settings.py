"""Environment-driven settings. Every backend flag has a local default (CLAUDE.md rule 8)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vl_store: Literal["memory", "sqlite", "firestore"] = "memory"
    vl_blob: Literal["local", "gcs"] = "local"
    vl_signer: Literal["local", "kms"] = "local"
    vl_screen: Literal["heuristic", "model_armor"] = "heuristic"

    vl_data_dir: Path = Path(".vl_data")
    vl_config_dir: Path = Path("config")
    active_config: str = "A"

    live_model: str = ""
    pro_model: str = ""
    flash_model: str = ""
    image_model: str = ""  # AI-enhanced product photos; empty = prompts only (reshoot tips)

    google_cloud_project: str = ""
    google_cloud_location: str = ""
    # Gemini through Vertex AI (deploy) instead of GOOGLE_API_KEY (local). ADK reads the same flag.
    google_genai_use_vertexai: bool = False
    gcs_bucket: str = ""
    # Full name (projects/P/locations/L/keyRings/R) or just the ring id (project/location above).
    kms_keyring: str = ""
    # Model Armor template id (or full name) used when VL_SCREEN=model_armor.
    model_armor_template: str = ""

    vendor_svc_url: str = "http://localhost:8001"
    retailer_svc_url: str = "http://localhost:8002"
    trust_gateway_url: str = "http://localhost:8000"
    mocks_url: str = "http://localhost:8009"

    # Bootstraps agent credentials at the gateway. Empty disables POST /credentials.
    vl_admin_token: str = ""
    # Enables /dev/* endpoints (reset, seed, tamper). Never enable in a deployed environment.
    vl_dev_endpoints: bool = False
    # Hosted demo: /dev/reset, /dev/tick and /dev/tamper answer only callers with the admin token
    # (tamper also the operator token). The dev outbox stays closed. ADR 0014.
    vl_demo_endpoints: bool = False
    token_ttl_seconds: int = 300
    # Gateway: calls per minute for an agent with no limit of its own (control API can set one).
    vl_rate_limit_per_minute: int = Field(default=120, ge=1)
    # Operators (people) using the control API from the console. Empty disables control actions.
    vl_operator_token: str = ""
    vl_trace: Literal["off", "console", "cloud"] = "off"

    # Human in the loop. Channels: "local" keeps Slack/Gmail messages in a dev outbox;
    # "mcp" sends them through the MCP servers configured by MCP_<SERVER>_* variables.
    vl_channels: Literal["local", "mcp"] = "local"
    vl_hitl_tick_seconds: int = Field(
        default=15, ge=0, description="0 disables the background loop"
    )
    vl_public_url: str = "http://localhost:5173"
    # Browser origins allowed to call the APIs (comma-separated). Deploy: the hosted web app only.
    vl_cors_origins: str = "*"
    # Durable job state (LangGraph): memory (tests), sqlite (local), postgres (deploy, Cloud SQL).
    vl_checkpoints: Literal["memory", "sqlite", "postgres"] = "sqlite"
    vl_checkpoint_dsn: str = ""
    service_name: str = "vendorlink"

    # A relay waits for the other side's agents (several model calls); other calls fail fast.
    vl_relay_timeout_seconds: float = Field(default=120, gt=0)
    log_level: str = "INFO"
    # pretty: one readable line per event (terminal); json: Cloud Logging; auto: by terminal
    vl_log_format: Literal["auto", "pretty", "json"] = "auto"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.vl_cors_origins.split(",") if o.strip()]


def export_dotenv(path: Path = Path(".env")) -> None:
    """Copy `.env` into the process environment, never overriding a variable already set.

    Settings read `.env` themselves, but the SDKs and MCP servers read `os.environ` directly
    (GOOGLE_API_KEY, MCP_*, SLACK_*, contact addresses in config). On Cloud Run there is no
    `.env` and everything already comes from the environment.
    """
    if path.is_file():
        from dotenv import load_dotenv

        load_dotenv(path, override=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    export_dotenv()
    return Settings()
