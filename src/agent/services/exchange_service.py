"""Exchange rate lookup with SQLite cache."""

import logging
import os

import requests  # type: ignore[import-untyped]

from agent.db import get_connection

logger = logging.getLogger(__name__)


class ExchangeService:
    """Fetches FX rates from exchangerate.host and caches them in the app DB."""

    def __init__(self) -> None:
        """Initialize with base URL from EXCHANGE_RATE_API_KEY env."""
        self.base_url = (
            f"https://api.exchangerate.host/convert?access_key={os.getenv('EXCHANGE_RATE_API_KEY')}"
        )

    def get_rate(self, date: str, from_currency: str, to_currency: str) -> float | None:
        """Return cached or fetched rate for date/currencies; None on failure."""
        conn = get_connection()
        cur = conn.execute(
            "SELECT rate FROM exchange_rates WHERE date = ? AND from_currency = ? AND to_currency = ?",
            (date, from_currency, to_currency),
        )
        row = cur.fetchone()
        cur.close()
        if row is not None:
            return float(row[0])

        try:
            response = requests.get(
                self.base_url,
                params={
                    "from": from_currency,
                    "to": to_currency,
                    "date": date,
                    "amount": 1,
                },
            )
            response.raise_for_status()
            data = response.json()
            rate = float(data["result"])
        except Exception as e:
            logger.error(
                "Failed to get exchange rate for %s %s to %s: %s",
                date,
                from_currency,
                to_currency,
                e,
            )
            return None

        conn.execute(
            "INSERT OR REPLACE INTO exchange_rates (date, from_currency, to_currency, rate) VALUES (?, ?, ?, ?)",
            (date, from_currency, to_currency, rate),
        )
        conn.commit()
        return rate
