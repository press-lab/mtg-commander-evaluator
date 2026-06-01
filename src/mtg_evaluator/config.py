from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://mtg_user:mtg_password@localhost:5432/mtg_evaluator"
    scryfall_rate_limit_ms: int = 100
    data_dir: str = "./data"
    log_level: str = "INFO"

    # Classifier selection: "deepseek" or "anthropic"
    classifier: str = "deepseek"

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Classification runner
    classifier_workers: int = 5
    classifier_retry_attempts: int = 3
    classifier_retry_delay_s: float = 2.0

    # DeepSeek via Anthropic SDK (tool use / structured output)
    deepseek_anthropic_base_url: str = "https://api.deepseek.com/anthropic"

    # EDHREC
    edhrec_top_url: str = "https://json.edhrec.com/pages/top/month.json"
    edhrec_top_n: int = 5000


settings = Settings()
