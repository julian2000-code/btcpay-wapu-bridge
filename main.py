"""
BTCPay → WapuPay bridge.

Flow:
1. Merchant creates a payment request via POST /create-payment
2. App asks WapuPay for a Lightning invoice (BOLT11)
3. Customer scans the BOLT11 QR code and pays
4. Sats land directly in WapuPay — no second transaction needed
5. WapuPay converts sats → ARS and sends to merchant's bank alias
6. BTCPay webhook fires for order confirmation/logging
"""

import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from btcpay import get_invoice, validate_webhook_signature
from wapu import create_lightning_invoice, get_lightning_address, get_quote, get_transactions

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
logger = logging.getLogger(__name__)

BTCPAY_WEBHOOK_SECRET = os.getenv("BTCPAY_WEBHOOK_SECRET")

app = FastAPI(
    title="BTCPay → WapuPay Bridge",
    description="Creates Lightning invoices via WapuPay so sats convert directly to ARS",
    version="2.0.0"
)


# --- Request model ---

class PaymentRequest(BaseModel):
    amount_sat: float
    description: str = "Payment"


# --- Endpoints ---

@app.get("/")
async def health_check():
    """Quick check that the server is running."""
    return {"status": "ok", "message": "BTCPay-Wapu bridge is running"}


@app.get("/lightning-address")
async def lightning_address():
    """
    Returns the WapuPay Lightning Address for this merchant.
    Format: username@wapu.app
    Customers can pay this address directly from any Lightning wallet.
    """
    try:
        address = await get_lightning_address()
        return {"lightning_address": address}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/create-payment")
async def create_payment(payment: PaymentRequest):
    """
    Creates a Lightning invoice via WapuPay.
    The customer pays this invoice — sats go directly to WapuPay
    who converts to ARS and sends to the merchant's bank.

    Returns the BOLT11 invoice string and ARS quote.
    """
    logger.info(f"Creating payment for {payment.amount_sat} sats")

    # Get ARS quote so merchant knows how much they will receive
    try:
        quote = await get_quote(payment.amount_sat)
        logger.info(f"Quote: {quote}")
    except Exception as e:
        logger.warning(f"Could not get quote: {e}")
        quote = {}

    # Create Lightning invoice via WapuPay
    try:
        invoice = await create_lightning_invoice(payment.amount_sat)
        logger.info(f"WapuPay invoice created: {invoice}")
    except Exception as e:
        logger.error(f"Failed to create WapuPay invoice: {e}")
        raise HTTPException(status_code=502, detail=f"WapuPay error: {str(e)}")

    return {
        "bolt11": invoice.get("payment_request") or invoice.get("bolt11") or invoice.get("invoice"),
        "amount_sat": payment.amount_sat,
        "ars_quote": quote,
        "wapu_response": invoice
    }


@app.get("/transactions")
async def transactions():
    """
    Returns all WapuPay transactions for this account.
    Use this to track payments received and ARS conversions.
    """
    try:
        result = await get_transactions()
        return {"transactions": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/webhook/btcpay")
async def btcpay_webhook(
    request: Request,
    btcpay_sig: str = Header(None, alias="BTCPay-Sig")
):
    """
    Receives webhook events from BTCPay Server.
    Used for order confirmation and logging — conversion
    happens automatically on WapuPay's side when sats arrive.
    """
    payload_bytes = await request.body()

    # Validate signature
    if not BTCPAY_WEBHOOK_SECRET:
        logger.warning("BTCPAY_WEBHOOK_SECRET not set — skipping signature check")
    elif not btcpay_sig:
        raise HTTPException(status_code=401, detail="Missing signature header")
    elif not validate_webhook_signature(payload_bytes, btcpay_sig, BTCPAY_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = json.loads(payload_bytes)
    event_type = event.get("type")
    invoice_id = event.get("invoiceId")

    logger.info(f"BTCPay event: {event_type} | Invoice: {invoice_id}")

    if event_type == "InvoiceSettled":
        logger.info(f"Payment confirmed for invoice {invoice_id} — ARS conversion handled by WapuPay")
        return {"status": "confirmed", "invoice_id": invoice_id}

    return {"status": "ignored", "event_type": event_type}
