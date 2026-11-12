import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp

from config import config
from database import settings

logger = logging.getLogger(__name__)

MAINNET_URL = "https://pay.crypt.bot/api"
TESTNET_URL = "https://testnet-pay.crypt.bot/api"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class CryptoBotError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Invoice:
    invoice_id: int
    status: str
    pay_url: str


async def get_token() -> str | None:
    # Токен из админки (БД) приоритетнее; .env служит запасным источником.
    db_token = await settings.get_setting(settings.CRYPTOBOT_TOKEN_KEY)
    if not db_token:
        return config.cryptobot_token
    return db_token


def _parse_invoice(raw: dict[str, Any]) -> Invoice:
    try:
        return Invoice(
            invoice_id=int(raw["invoice_id"]),
            status=str(raw["status"]),
            pay_url=raw.get("bot_invoice_url") or raw.get("pay_url", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CryptoBotError(f"Неожиданный формат счёта: {exc!r}") from exc


async def _call(token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    # Crypto Pay API принимает токен приложения в заголовке; любой метод
    # доступен через POST с JSON-телом. Ошибки приходят как {"ok": false, "error": {...}},
    # причём mainnet и testnet — разные серверы: токен одной сети на другой даёт 401.
    base_url = TESTNET_URL if config.cryptobot_testnet else MAINNET_URL
    headers = {"Crypto-Pay-API-Token": token}
    status: int | None = None

    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.post(f"{base_url}/{method}", headers=headers, json=params or {}) as response:
                status = response.status
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise CryptoBotError(
            f"{method}: сетевая ошибка или некорректный ответ ({exc})", status=status
        ) from exc

    logger.debug("CryptoBot %s (%s): HTTP %s", method, base_url, status)

    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else payload
        raise CryptoBotError(f"{method}: {error}", status=status)
    if "result" not in payload:
        raise CryptoBotError(f"{method}: в ответе нет поля result", status=status)
    return payload["result"]


async def get_me(token: str) -> str:
    result = await _call(token, "getMe")
    name = result.get("name") if isinstance(result, dict) else None
    return str(name or "—")


async def create_invoice(
    token: str,
    amount: Decimal,
    description: str,
    payload: str,
    expires_in: int,
) -> Invoice:
    # currency_type="fiat" — цена выставляется в рублях, а CryptoBot сам пересчитывает
    # её в актив, который выберет покупатель, по курсу на момент оплаты.
    result = await _call(
        token,
        "createInvoice",
        {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": f"{amount:.2f}",
            "description": description[:1024],
            "payload": payload,
            "expires_in": expires_in,
        },
    )
    invoice = _parse_invoice(result)
    logger.info("Создан счёт CryptoBot %s на %s RUB", invoice.invoice_id, amount)
    return invoice


async def get_invoice(token: str, invoice_id: int) -> Invoice | None:
    result = await _call(token, "getInvoices", {"invoice_ids": str(invoice_id)})
    # В зависимости от версии API ответ приходит списком либо объектом с ключом items.
    items = result.get("items", []) if isinstance(result, dict) else result
    return _parse_invoice(items[0]) if items else None
