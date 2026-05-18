"""
BTCPay Server client.
Handles webhook signature validation, invoice lookups, and data aggregation.
"""

import hashlib
import hmac
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BTCPAY_URL = os.getenv("BTCPAY_URL", "http://127.0.0.1:8080")
BTCPAY_API_KEY = os.getenv("BTCPAY_API_KEY")
BTCPAY_STORE_ID = os.getenv("BTCPAY_STORE_ID")

HEADERS = {"Authorization": f"token {BTCPAY_API_KEY}"}


def validate_webhook_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    """
    Validate that a webhook request genuinely came from BTCPay Server.
    BTCPay signs the raw payload with HMAC-SHA256 using your webhook secret.
    """
    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature_header)


async def get_invoice(invoice_id: str) -> dict:
    """Fetch full invoice details from BTCPay Server."""
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/invoices/{invoice_id}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()


async def get_invoices() -> list:
    """Fetch all invoices for this store."""
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/invoices"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()


async def get_store_info() -> dict:
    """Fetch store name and details."""
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()


async def get_lightning_balance() -> dict:
    """Fetch Lightning node balance."""
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/lightning/BTC/balance"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()


async def create_btcpay_lightning_invoice(amount_sat: float, description: str = "Payment") -> dict:
    """
    Create a Lightning invoice via BTCPay's connected LND node.
    Used for above-threshold payments so sats land directly in the Lightning wallet.
    """
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/lightning/BTC/invoices"
    amount_msat = int(amount_sat * 1000)  # BTCPay expects millisatoshis
    payload = {"amount": amount_msat, "description": description, "expiry": 3600}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=HEADERS)
        response.raise_for_status()
        return response.json()


async def pay_lightning_invoice(bolt11: str) -> dict:
    """
    Pay a Lightning invoice from BTCPay's connected LND node.
    Used after a BTCPay payment settles to send the ARS-conversion portion to WapuPay.
    """
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/lightning/BTC/invoices/pay"
    payload = {"BOLT11": bolt11}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=HEADERS)
        response.raise_for_status()
        return response.json()


async def get_payouts() -> list:
    """Fetch all payouts for this store."""
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/payouts"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
