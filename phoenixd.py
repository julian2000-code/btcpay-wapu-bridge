"""
phoenixd client for SatTope.
phoenixd is the merchant's Lightning wallet — it receives sats for above-threshold payments.
Connects to the local phoenixd REST API at 127.0.0.1:9740.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

PHOENIXD_URL = os.getenv("PHOENIXD_URL", "http://127.0.0.1:9740")
PHOENIXD_PASSWORD = os.getenv("PHOENIXD_PASSWORD", "")

# phoenixd uses HTTP Basic auth: empty username, password from phoenix.conf
def _auth() -> tuple:
    return ("", PHOENIXD_PASSWORD)


async def get_phoenixd_info() -> dict:
    """Get node info — used to check phoenixd is running."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{PHOENIXD_URL}/getinfo", auth=_auth())
        response.raise_for_status()
        return response.json()


async def create_phoenixd_invoice(amount_sat: int, description: str = "Payment") -> dict:
    """
    Create a Lightning invoice via phoenixd.
    Returns the BOLT11 invoice string and payment hash.
    phoenixd handles liquidity automatically — no channel management needed.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PHOENIXD_URL}/createinvoice",
            auth=_auth(),
            data={
                "amountSat": int(amount_sat),
                "description": description,
                "expirySeconds": 3600,
            }
        )
        response.raise_for_status()
        return response.json()


async def get_phoenixd_invoice(payment_hash: str) -> dict:
    """
    Look up an invoice by payment hash to check if it's been paid.
    Returns invoice details including isPaid status.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PHOENIXD_URL}/payments/incoming/{payment_hash}",
            auth=_auth()
        )
        response.raise_for_status()
        return response.json()


async def get_phoenixd_balance() -> dict:
    """
    Get the current Lightning balance in phoenixd.
    Returns balanceSat (spendable) and feeCreditSat.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{PHOENIXD_URL}/getinfo", auth=_auth())
        response.raise_for_status()
        data = response.json()
        return {
            "local": data.get("channelsBalanceSat", 0),
            "node_id": data.get("nodeId", ""),
        }
