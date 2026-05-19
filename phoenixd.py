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


async def pay_phoenixd_invoice(bolt11: str) -> dict:
    """
    Pay a Lightning invoice from phoenixd.
    Used to send sats from the merchant wallet to WapuPay for USDT conversion.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PHOENIXD_URL}/payinvoice",
            auth=_auth(),
            data={"invoice": bolt11}
        )
        response.raise_for_status()
        return response.json()


async def list_phoenixd_payments(limit: int = 50) -> list:
    """
    List all incoming Lightning payments received by phoenixd.
    Returns paid invoices with amount, description, and timestamp.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PHOENIXD_URL}/payments/incoming",
            auth=_auth(),
            params={"limit": limit}
        )
        response.raise_for_status()
        return response.json()


async def get_phoenixd_balance() -> dict:
    """
    Get the current Lightning balance in phoenixd.
    Sums balanceSat across all open channels.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{PHOENIXD_URL}/getinfo", auth=_auth())
        response.raise_for_status()
        data = response.json()
        channels = data.get("channels", [])
        total_sat = sum(ch.get("balanceSat", 0) for ch in channels)
        return {
            "local": total_sat,
            "node_id": data.get("nodeId", ""),
            "channels": len(channels),
        }
