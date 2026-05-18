"""
Direct LND node client.
Connects to the LND REST API via the socat tunnel (127.0.0.1:8183).
Used for above-threshold payments so invoices are real mainnet BOLT11.
"""

import base64
import os
import urllib.parse

import httpx
from dotenv import load_dotenv

load_dotenv()

LND_CONNECT_URL = os.getenv("LND_CONNECT_URL", "")
LND_REST_HOST = os.getenv("LND_REST_HOST", "127.0.0.1:8183")


def _get_macaroon_hex() -> str:
    """Extract macaroon from LND_CONNECT_URL and return as hex string."""
    parsed = urllib.parse.urlparse(LND_CONNECT_URL)
    params = urllib.parse.parse_qs(parsed.query)
    macaroon_b64 = params.get("macaroon", [""])[0]
    if not macaroon_b64:
        raise ValueError("No macaroon found in LND_CONNECT_URL")
    macaroon_bytes = base64.urlsafe_b64decode(macaroon_b64 + "==")
    return macaroon_bytes.hex()


def _get_headers() -> dict:
    return {
        "Grpc-Metadata-Macaroon": _get_macaroon_hex(),
        "Content-Type": "application/json"
    }


async def create_lnd_invoice(amount_sat: float, description: str = "Payment") -> dict:
    """
    Create a real mainnet Lightning invoice directly on the LND node.
    Returns payment_request (BOLT11) and r_hash (for payment status polling).
    """
    url = f"https://{LND_REST_HOST}/v1/invoices"
    payload = {
        "value": int(amount_sat),
        "memo": description,
        "expiry": 3600,
    }
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(url, json=payload, headers=_get_headers())
        response.raise_for_status()
        return response.json()


async def get_lnd_invoice(r_hash_hex: str) -> dict:
    """
    Check the status of an LND invoice by its payment hash.
    Used for polling — returns settled=True when customer has paid.
    """
    url = f"https://{LND_REST_HOST}/v1/invoice/{r_hash_hex}"
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(url, headers=_get_headers())
        response.raise_for_status()
        return response.json()


async def pay_lnd_invoice(bolt11: str) -> dict:
    """
    Pay a Lightning invoice from the LND node.
    Used to send the ARS-conversion portion to WapuPay after a split payment.
    """
    url = f"https://{LND_REST_HOST}/v1/channels/transactions"
    payload = {
        "payment_request": bolt11,
        "fee_limit": {"fixed": "10000"}  # max 10,000 msat routing fee
    }
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(url, json=payload, headers=_get_headers())
        response.raise_for_status()
        return response.json()
