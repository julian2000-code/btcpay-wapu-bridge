"""
BTCPay Server client.
Handles webhook signature validation and invoice lookups.
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


def validate_webhook_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    """
    Validate that a webhook request genuinely came from BTCPay Server.
    BTCPay signs the raw payload with HMAC-SHA256 using your webhook secret.

    Args:
        payload_bytes:     The raw request body as bytes
        signature_header:  The value of the BTCPay-Sig header
        secret:            Your webhook secret from BTCPay

    Returns:
        True if the signature is valid, False otherwise
    """
    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()

    # BTCPay sends the header as "sha256=<hash>"
    return hmac.compare_digest(f"sha256={expected}", signature_header)


async def get_invoice(invoice_id: str) -> dict:
    """
    Fetch full invoice details from BTCPay Server.

    Args:
        invoice_id: The BTCPay invoice ID from the webhook payload

    Returns:
        Invoice data as a dictionary
    """
    url = f"{BTCPAY_URL}/api/v1/stores/{BTCPAY_STORE_ID}/invoices/{invoice_id}"
    headers = {"Authorization": f"token {BTCPAY_API_KEY}"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
