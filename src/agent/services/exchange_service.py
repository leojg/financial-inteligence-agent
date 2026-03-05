import logging
import os
from datetime import datetime, timezone

import requests

from agent.db import get_connection

logger = logging.getLogger(__name__)


class ExchangeService:
    def __init__(self):
        self.base_url = (
            f"https://api.exchangerate.host/convert?access_key={os.getenv('EXCHANGE_RATE_API_KEY')}"
        )

    def get_rate(self, date: str, from_currency: str, to_currency: str) -> float | None:
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
            rate = response.json()["result"]
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
