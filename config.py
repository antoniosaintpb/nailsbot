from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./data/nailsbot.db"
    master_telegram_ids: str = ""
    timezone: str = "Europe/Moscow"

    @property
    def master_ids(self) -> set[int]:
        raw = (self.master_telegram_ids or "").replace(" ", "")
        if not raw:
            return set()
        out: set[int] = set()
        for part in raw.split(","):
            if part.strip().isdigit():
                out.add(int(part.strip()))
        return out

    def is_master(self, telegram_id: int) -> bool:
        return telegram_id in self.master_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
