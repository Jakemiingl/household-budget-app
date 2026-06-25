"""Application configuration, loaded from environment / .env file.

All secrets (Plaid keys) live in .env, which is git-ignored. Nothing here
reaches out anywhere except Plaid (bank sync) and the local Claude CLI.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Plaid
    plaid_env: str = "sandbox"  # sandbox | production
    plaid_client_id: str = ""
    plaid_secret: str = ""

    # Claude CLI (AI chat) — uses your Claude subscription, not the API.
    claude_cli_path: str = "claude"
    claude_model: str = "haiku"

    # Telegram bot (optional) — message the assistant from your phones.
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""  # comma-separated chat IDs allowed to use it

    # App
    app_host: str = "127.0.0.1"
    app_port: int = 8765

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "budget.db"

    @property
    def plaid_configured(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)

    @property
    def telegram_allowed_set(self) -> set[int]:
        out: set[int] = set()
        for part in self.telegram_allowed_chat_ids.split(","):
            part = part.strip()
            if part:
                try:
                    out.add(int(part))
                except ValueError:
                    pass
        return out


settings = Settings()
DATA_DIR.mkdir(exist_ok=True)
