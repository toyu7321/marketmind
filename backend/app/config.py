from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarketMind API"
    environment: str = "development"
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
    enable_live_trading: bool = False
    cors_origins: str = "http://localhost:3000"
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
