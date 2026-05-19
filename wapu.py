"""
WapuPay API client.
Handles authentication, Lightning invoice creation, and ARS conversion.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

WAPU_API_BASE_URL = os.getenv("WAPU_API_BASE_URL", "https://be-stage.wapu.app")
WAPU_API_KEY = os.getenv("WAPU_API_KEY")
WAPU_EMAIL = os.getenv("WAPU_EMAIL")
WAPU_PASSWORD = os.getenv("WAPU_PASSWORD")
WAPU_WITHDRAW_ALIAS = os.getenv("WAPU_WITHDRAW_ALIAS")
WAPU_RECEIVER_NAME = os.getenv("WAPU_RECEIVER_NAME")


async def get_auth_headers() -> dict:
    """
    Return the correct auth headers.
    Uses API key from .env if set, otherwise logs in with email/password.
    """
    if WAPU_API_KEY:
        return {"X-API-Key": WAPU_API_KEY, "Accept": "application/json"}

    # Fallback: login with email/password to get a JWT
    url = f"{WAPU_API_BASE_URL}/users/login"
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json={"email": WAPU_EMAIL, "password": WAPU_PASSWORD})
        response.raise_for_status()
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def get_wapu_home() -> dict:
    """
    Get WapuPay account info including USDT balance, ARS equivalent, and exchange rates.
    """
    url = f"{WAPU_API_BASE_URL}/users/home"
    headers = await get_auth_headers()
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_lightning_address() -> str:
    """
    Get the WapuPay Lightning Address for this account.
    Format: username@wapu.app
    """
    url = f"{WAPU_API_BASE_URL}/users/home"
    headers = await get_auth_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        username = data.get("username", "").strip().lower()
        if not username:
            raise ValueError("WapuPay did not return a username")
        return f"{username}@wapu.app"


async def get_quote(amount_sat: float, transfer_type: str = "fast_fiat_transfer") -> dict:
    """
    Get a conversion quote: how much ARS the customer will receive for a given amount of sats.

    Args:
        amount_sat:    Amount in satoshis
        transfer_type: "fast_fiat_transfer" (<2h) or "fiat_transfer" (<24h)

    Returns:
        Quote data including ARS amount
    """
    url = f"{WAPU_API_BASE_URL}/transactions/tentative-amount"
    headers = await get_auth_headers()
    payload = {
        "amount": amount_sat,
        "currency_payment": "ARS",
        "currency_taken": "SAT",
        "type": transfer_type
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_transaction(transaction_id: str) -> dict:
    """
    Fetch the status of a single WapuPay transaction.
    Used to poll for Lightning invoice payment confirmation.
    """
    url = f"{WAPU_API_BASE_URL}/transactions/{transaction_id}"
    headers = await get_auth_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_transactions() -> dict:
    """
    Fetch all WapuPay transactions for this account.
    Shows payment history, statuses, and ARS conversion amounts.
    """
    url = f"{WAPU_API_BASE_URL}/transactions/my_transactions"
    headers = await get_auth_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()



async def create_lightning_invoice(amount_sat: float) -> dict:
    """
    Ask WapuPay to generate a Lightning invoice (BOLT11).
    The customer pays this invoice directly — sats land in WapuPay
    and ARS conversion happens automatically on their side.

    Args:
        amount_sat: Amount in satoshis the customer should pay

    Returns:
        Response from WapuPay including the BOLT11 invoice string
    """
    url = f"{WAPU_API_BASE_URL}/wallet/deposit_lightning"
    headers = await get_auth_headers()
    payload = {
        "amount": amount_sat,
        "currency": "SAT"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
