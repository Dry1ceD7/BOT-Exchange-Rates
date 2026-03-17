import asyncio
from datetime import date
from core.api_client import BOTClient
import httpx
from dotenv import load_dotenv

load_dotenv()

async def test():
    async with httpx.AsyncClient() as c:
        bc = BOTClient(c)
        print("Fetching USD...")
        d = await bc.get_exchange_rates(date(2025, 1, 1), date.today(), "USD")
        print("USD fetched:", len(d))
        print("Fetching EUR...")
        d = await bc.get_exchange_rates(date(2025, 1, 1), date.today(), "EUR")
        print("EUR fetched:", len(d))

asyncio.run(test())
