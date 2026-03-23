"""Exchange rate lookup with DB cache."""

import logging
import os

import requests  # type: ignore[import-untyped]
from sqlalchemy import text

from shared.db import get_session

logger = logging.getLogger(__name__)


class ExchangeRepository:
    """Fetches FX rates from exchangerate.host and caches them in the app DB."""

    def __init__(self) -> None:
        """Initialize with base URL from EXCHANGE_RATE_API_KEY env."""
        self.base_url = f"https://api.exchangerate.host/convert?access_key={os.getenv('EXCHANGE_RATE_API_KEY')}"

    def get_rate(self, date: str, from_currency: str, to_currency: str) -> float | None:
        """Return cached or fetched rate for date/currencies; None on failure."""
        with get_session() as session:
            result = session.execute(
                text(
                    "SELECT rate FROM exchange_rates WHERE date = :d AND from_currency = :f AND to_currency = :t"
                ),
                {"d": date, "f": from_currency, "t": to_currency},
            )
            row = result.fetchone()
            if row is not None:
                return float(row[0])

            try:
                response = requests.get(
                    self.base_url,
                    params={
                        "from": from_currency,
                        "to": to_currency,
                        "date": date,
                        "amount": "1",
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

            session.execute(
                text(
                    "INSERT INTO exchange_rates (date, from_currency, to_currency, rate) "
                    "VALUES (:d, :f, :t, :rate) "
                    "ON CONFLICT (date, from_currency, to_currency) DO UPDATE SET rate = :rate"
                ),
                {"d": date, "f": from_currency, "t": to_currency, "rate": rate},
            )
            session.commit()
        return rate
