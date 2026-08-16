from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
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
        return self

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
