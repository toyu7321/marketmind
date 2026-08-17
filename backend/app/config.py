from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarketMind API"
    environment: str = Field(default="development", validation_alias=AliasChoices("MARKETMIND_ENV", "ENVIRONMENT"))
    database_url: str = "sqlite+aiosqlite:///./marketmind.db"
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    enable_openai_analysis: bool = False
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_feed: str = "iex"
    alpaca_options_feed: str = "indicative"
    market_data_request_timeout_seconds: int = 8
    market_data_stale_seconds: int = 900
    alpaca_oauth_client_id: str = ""
    alpaca_oauth_client_secret: str = ""
    alpaca_oauth_redirect_uri: str = ""
    sec_user_agent: str = "MarketMind local-user contact@example.com"
    enable_paper_trading: bool = True
    enable_remote_paper_orders: bool = False
    enable_live_trading: bool = False
    cors_origins: str = "http://localhost:3000"
    frontend_origin: str = ""

    # Authentication is intentionally fail-closed. There is no anonymous runtime
    # mode; tests use FastAPI dependency overrides rather than a hidden bypass.
    auth_mode: str = "required"
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    supabase_jwt_issuer: str = ""
    supabase_jwks_url: str = ""
    auth_jwt_audience: str = "authenticated"
    auth_jwt_algorithms: str = "ES256,RS256"
    auth_jwks_cache_seconds: int = 300
    admin_mfa_required: bool = True
    audit_ip_hmac_secret: str = ""
    rate_limit_per_minute: int = 120
    # A deliberately short-lived remote-first-admin escape hatch. It never
    # enables itself and must be removed from the host after one successful use.
    bootstrap_admin_enabled: bool = False
    bootstrap_admin_secret: str = ""
    bootstrap_admin_rate_limit_per_hour: int = 3
    auto_create_schema: bool | None = None

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]
        if self.frontend_origin.strip():
            origins.append(self.frontend_origin.strip().rstrip("/"))
        return list(dict.fromkeys(origins))

    @property
    def auth_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key and self.auth_mode.lower() == "required")

    @property
    def jwt_issuer(self) -> str:
        return self.supabase_jwt_issuer.rstrip("/") or f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return self.supabase_jwks_url or f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_algorithms(self) -> list[str]:
        return [algorithm.strip() for algorithm in self.auth_jwt_algorithms.split(",") if algorithm.strip()]

    @property
    def should_auto_create_schema(self) -> bool:
        return (not self.is_production) if self.auto_create_schema is None else self.auto_create_schema

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.auth_mode.lower() != "required":
            raise ValueError("AUTH_MODE must be required; anonymous authentication modes are not supported.")
        if self.is_production:
            origins = self.cors_origin_list
            if not origins or "*" in origins:
                raise ValueError("Production requires explicit HTTPS CORS_ORIGINS or FRONTEND_ORIGIN; wildcard origins are not allowed.")
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production CORS origins must use HTTPS.")
            if not self.auth_ready:
                raise ValueError("Production requires AUTH_MODE=required and configured Supabase Auth credentials.")
            if not self.supabase_secret_key:
                raise ValueError("Production requires SUPABASE_SECRET_KEY for server-side invite administration.")
            if self.enable_live_trading:
                raise ValueError("ENABLE_LIVE_TRADING must remain false until a separately reviewed release.")
            if not self.audit_ip_hmac_secret:
                raise ValueError("Production requires AUDIT_IP_HMAC_SECRET for privacy-preserving audit metadata.")
        if self.bootstrap_admin_enabled and len(self.bootstrap_admin_secret) < 32:
            raise ValueError("BOOTSTRAP_ADMIN_ENABLED requires a BOOTSTRAP_ADMIN_SECRET of at least 32 characters.")
        if self.bootstrap_admin_rate_limit_per_hour < 1 or self.bootstrap_admin_rate_limit_per_hour > 10:
            raise ValueError("BOOTSTRAP_ADMIN_RATE_LIMIT_PER_HOUR must be between 1 and 10.")
        return self

    @field_validator("alpaca_feed")
    @classmethod
    def validate_alpaca_feed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"iex", "sip", "delayed_sip"}:
            raise ValueError("ALPACA_FEED must be one of iex, sip, or delayed_sip.")
        return normalized

    @field_validator("alpaca_options_feed")
    @classmethod
    def validate_alpaca_options_feed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"indicative", "opra"}:
            raise ValueError("ALPACA_OPTIONS_FEED must be indicative or opra.")
        return normalized

    @field_validator("market_data_request_timeout_seconds")
    @classmethod
    def validate_market_data_timeout(cls, value: int) -> int:
        if not 1 <= value <= 30:
            raise ValueError("MARKET_DATA_REQUEST_TIMEOUT_SECONDS must be between 1 and 30.")
        return value

    @field_validator("market_data_stale_seconds")
    @classmethod
    def validate_market_data_stale_seconds(cls, value: int) -> int:
        if not 60 <= value <= 86_400:
            raise ValueError("MARKET_DATA_STALE_SECONDS must be between 60 and 86400.")
        return value

    @property
    def demo_mode(self) -> bool:
        return not bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key and self.enable_openai_analysis)

    @property
    def sec_is_configured(self) -> bool:
        return "contact@example.com" not in self.sec_user_agent.lower()

    @property
    def paper_order_submission_enabled(self) -> bool:
        # Remote paper orders stay explicitly opt-in after authentication is configured.
        return self.enable_paper_trading and self.auth_ready and self.enable_remote_paper_orders and not self.enable_live_trading


@lru_cache
def get_settings() -> Settings:
    return Settings()
