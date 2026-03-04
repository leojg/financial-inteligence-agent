import requests
import logging
import os

logger = logging.getLogger(__name__)

class ExchangeService:
    def __init__(self):
        self.base_url = f"https://api.exchangerate.host/convert?access_key={os.getenv('EXCHANGE_RATE_API_KEY')}"

    def get_rate(self, date: str, from_currency: str, to_currency: str) -> float:
        try:
            response = requests.get(
                self.base_url,
                params={"from": from_currency, "to": to_currency, "date": date, "amount": 1}
            )
            response.raise_for_status()
            return response.json()["result"]
        except Exception as e:
            logger.error(f"Failed to get exchange rate for {date} {from_currency} to {to_currency}: {e}")
            return None