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
    sec_user_agent: str = "MarketMind local-user contact@example.com"
    enable_paper_trading: bool = True
    enable_remote_paper_orders: bool = False
    enable_live_trading: bool = False
    cors_origins: str = "http://localhost:3000"
    frontend_origin: str = ""
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]
        if self.frontend_origin.strip():
            origins.append(self.frontend_origin.strip().rstrip("/"))
        return list(dict.fromkeys(origins))

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.environment.lower() in {"production", "prod"}:
            origins = self.cors_origin_list
            if not origins or "*" in origins:
                raise ValueError("Production requires explicit HTTPS CORS_ORIGINS or FRONTEND_ORIGIN; wildcard origins are not allowed.")
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production CORS origins must use HTTPS.")
        return self

    @property
    def demo_mode(self) -> bool:
        return not bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key and self.enable_openai_analysis)

    @property
    def sec_is_configured(self) -> bool:
        """Avoid calling EDGAR with the example identity from .env.example."""
        return "contact@example.com" not in self.sec_user_agent.lower()

    @property
    def paper_order_submission_enabled(self) -> bool:
        return self.enable_paper_trading and (self.environment.lower() not in {"production", "prod"} or self.enable_remote_paper_orders)


@lru_cache
def get_settings() -> Settings:
    return Settings()
