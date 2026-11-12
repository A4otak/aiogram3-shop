import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: tuple[int, ...]
    support_username: str
    cryptobot_token: str | None
    db_path: Path
    log_level: str
    cryptobot_testnet: bool
    invoice_ttl: int

    @property
    def support_url(self) -> str | None:
        return f"https://t.me/{self.support_username}" if self.support_username else None


def _parse_ids(raw: str) -> tuple[int, ...]:
    return tuple(int(item) for item in raw.replace(" ", "").split(",") if item)


def _parse_bool(raw: str) -> bool:
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def _parse_username(raw: str) -> str:
    return raw.strip().removeprefix("@")


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните его.")

    db_path = Path(os.getenv("DB_PATH", "data/bot.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Config(
        bot_token=token,
        admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "")),
        support_username=_parse_username(os.getenv("SUPPORT_USERNAME", "")),
        cryptobot_token=os.getenv("CRYPTOBOT_TOKEN", "").strip() or None,
        db_path=db_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        cryptobot_testnet=_parse_bool(os.getenv("CRYPTOBOT_TESTNET", "false")),
        invoice_ttl=int(os.getenv("INVOICE_TTL_SECONDS", "1800")),
    )


config = load_config()
