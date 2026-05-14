"""
BTCPay → WapuPay bridge.

Flow:
1. Customer visits the webstore and picks a product
2. At checkout, app asks WapuPay for a Lightning invoice (BOLT11)
3. Customer scans the BOLT11 QR code and pays with their Lightning wallet
4. Sats land directly in WapuPay — no second transaction needed
5. WapuPay converts sats → ARS and sends to merchant's bank alias
6. BTCPay webhook fires for order confirmation/logging
"""

import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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

# Serve static files (CSS) and HTML templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- Request model ---

class PaymentRequest(BaseModel):
    amount_sat: float
    description: str = "Payment"


# --- Store pages ---

@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    """Webstore homepage."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/product", response_class=HTMLResponse)
async def product_page(request: Request):
    """Product detail page."""
    return templates.TemplateResponse(request, "product.html")


@app.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request):
    """Checkout page with Lightning payment."""
    return templates.TemplateResponse(request, "checkout.html")


@app.get("/confirmation", response_class=HTMLResponse)
async def confirmation_page(request: Request):
    """Payment confirmation + ARS conversion summary."""
    return templates.TemplateResponse(request, "confirmation.html")


# --- API endpoints ---

@app.get("/lightning-address")
async def lightning_address():
    """
    Returns the WapuPay Lightning Address for this merchant.
    Format: username@wapu.app
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
        "bolt11": invoice.get("lnurl_pr_invoice") or invoice.get("payment_request") or invoice.get("bolt11"),
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
    Used for order confirmation and logging.
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
