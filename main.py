"""
BTCPay → WapuPay bridge.

Flow:
1. Customer visits the webstore and picks a product
2. At checkout, app asks WapuPay for a Lightning invoice (BOLT11)
3. Customer scans the BOLT11 QR code and pays with their Lightning wallet
4. Sats land directly in WapuPay — no second transaction needed
5. WapuPay applies threshold logic:
   - Below threshold → converts sats to ARS, sends to merchant bank
   - Above threshold → keeps sats in BTCPay Lightning wallet
6. BTCPay webhook fires for order confirmation and threshold tracking
"""

import base64
import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from btcpay import get_invoice, get_invoices, get_lightning_balance, get_payouts, get_store_info, validate_webhook_signature
from lnd import create_lnd_invoice, get_lnd_invoice, pay_lnd_invoice
from threshold import get_status, record_conversion, record_kept, should_convert, update_threshold
from wapu import convert_to_ars, create_lightning_invoice, get_lightning_address, get_quote, get_transaction, get_transactions

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
logger = logging.getLogger(__name__)

BTCPAY_WEBHOOK_SECRET = os.getenv("BTCPAY_WEBHOOK_SECRET")

app = FastAPI(
    title="BTCPay → WapuPay Bridge",
    description="Lightning payments with smart ARS conversion threshold",
    version="3.0.0"
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


@app.get("/plugin", response_class=HTMLResponse)
async def plugin_page(request: Request):
    """BTCPay plugin mockup — WapuPay ARS Settlement dashboard."""
    return templates.TemplateResponse(request, "plugin.html")


# --- API endpoints ---

@app.get("/lightning-address")
async def lightning_address():
    """Returns the WapuPay Lightning Address for this merchant."""
    try:
        address = await get_lightning_address()
        return {"lightning_address": address}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/threshold")
async def threshold_status():
    """
    Returns current month's conversion threshold status.
    Shows how many sats have been converted vs kept in Lightning.
    """
    return get_status()


class ThresholdUpdate(BaseModel):
    threshold_sat: int


@app.post("/threshold/update")
async def update_threshold_endpoint(body: ThresholdUpdate):
    """Update the monthly conversion threshold. Persists across restarts."""
    if body.threshold_sat < 1000:
        raise HTTPException(status_code=400, detail="Threshold must be at least 1,000 sats")
    update_threshold(body.threshold_sat)
    logger.info(f"Threshold updated to {body.threshold_sat} sats")
    return get_status()


@app.get("/api/dashboard")
async def dashboard():
    """
    Aggregates real data from BTCPay and WapuPay for the plugin page.
    Returns invoices, Lightning balance, WapuPay transactions, and threshold status.
    """
    # Fetch from BTCPay
    try:
        store = await get_store_info()
        store_name = store.get("name", "My Store")
    except Exception:
        store_name = "My Store"

    try:
        invoices = await get_invoices()
    except Exception:
        invoices = []

    try:
        lightning_balance = await get_lightning_balance()
    except Exception:
        lightning_balance = {}

    # Fetch from WapuPay
    try:
        wapu_txs = await get_transactions()
        if isinstance(wapu_txs, dict):
            wapu_txs = wapu_txs.get("transactions", []) or wapu_txs.get("items", []) or []
    except Exception:
        wapu_txs = []

    # Threshold status
    threshold = get_status()

    # Build a lookup of WapuPay transactions by transaction_id for cross-referencing
    wapu_tx_lookup = {}
    for tx in wapu_txs:
        tx_id = tx.get("transaction_id") or tx.get("id")
        if tx_id:
            wapu_tx_lookup[str(tx_id)] = tx

    # Build unified payment list from BTCPay settled invoices
    payments = []
    for inv in invoices:
        if inv.get("status") == "Settled":
            amount_sat = float(inv.get("amount", 0))
            payments.append({
                "date": inv.get("createdTime", ""),
                "invoice_id": inv.get("id", ""),
                "amount_sat": amount_sat,
                "currency": inv.get("currency", "SATS"),
                "status": "Settled",
                "ars_amount": None
            })

    # Build ARS payouts from threshold payments, enriched with live WapuPay data
    ars_payouts = []
    for p in threshold.get("payments", []):
        tx_id = str(p.get("invoice_id", ""))
        wapu_tx = wapu_tx_lookup.get(tx_id, {})
        wapu_status = wapu_tx.get("status", "").upper() if wapu_tx else ""
        is_completed = wapu_status in ("COMPLETED", "DONE", "PAID", "SUCCESS")

        # Use live ARS amount from WapuPay if available, fall back to recorded amount
        live_ars = float(wapu_tx.get("payment_amount") or 0) if wapu_tx else 0
        ars_amount = live_ars if live_ars > 0 else float(p.get("ars_amount") or 0)

        ars_payouts.append({
            "date": p.get("date", ""),
            "invoice_id": tx_id,
            "amount_sat": p.get("amount_sat", 0),
            "ars_amount": ars_amount if is_completed or ars_amount > 0 else 0,
            "type": p.get("type", "converted"),
            "wapu_status": wapu_status or ("COMPLETED" if ars_amount > 0 else "PENDING")
        })

    return {
        "store_name": store_name,
        "lightning_balance": lightning_balance,
        "btcpay_invoices": invoices,
        "ars_payouts": ars_payouts,
        "threshold": threshold,
        "wapu_transactions": wapu_txs
    }


@app.post("/create-payment")
async def create_payment(payment: PaymentRequest):
    """
    Creates a Lightning invoice — either via WapuPay (below threshold)
    or via BTCPay's LND node (above threshold).

    Below threshold: WapuPay generates invoice → sats convert to ARS automatically.
    Above threshold: BTCPay LND generates invoice → sats land in Lightning wallet.
    If payment would split the threshold, BTCPay generates the invoice and the
    webhook handles sending the ARS portion to WapuPay after settlement.
    """
    logger.info(f"Creating payment for {payment.amount_sat} sats")

    # Check threshold to decide invoice source
    will_convert, convert_sat, keep_sat = should_convert(payment.amount_sat)
    use_btcpay = keep_sat > 0  # True if any sats would be kept (at or over threshold)

    # Get ARS quote
    try:
        quote = await get_quote(min(convert_sat, payment.amount_sat) if convert_sat > 0 else payment.amount_sat)
    except Exception as e:
        logger.warning(f"Could not get quote: {e}")
        quote = {}

    if use_btcpay:
        # Above threshold — LND node generates the invoice directly (real mainnet BOLT11)
        # App polls LND for settlement, then sends ARS portion to WapuPay
        try:
            invoice = await create_lnd_invoice(payment.amount_sat, payment.description)
            logger.info(f"LND invoice created: {invoice}")
            bolt11 = invoice.get("payment_request")
            r_hash = invoice.get("r_hash", "")
            # r_hash comes as base64 from LND REST — convert to hex for polling
            try:
                r_hash_hex = base64.b64decode(r_hash).hex()
            except Exception:
                r_hash_hex = r_hash
            return {
                "bolt11": bolt11,
                "amount_sat": payment.amount_sat,
                "invoice_source": "lnd",
                "r_hash": r_hash_hex,
                "convert_sat": convert_sat,
                "keep_sat": keep_sat,
                "ars_quote": quote,
                "threshold_info": {
                    "will_convert_sat": convert_sat,
                    "will_keep_sat": keep_sat,
                    "message": f"LND receives payment. Converting {convert_sat} sats to ARS, keeping {keep_sat} sats in Lightning."
                }
            }
        except Exception as e:
            logger.error(f"Failed to create LND invoice: {e}")
            raise HTTPException(status_code=502, detail=f"LND error: {str(e)}")
    else:
        # Below threshold — WapuPay generates the invoice → auto-converts to ARS
        try:
            invoice = await create_lightning_invoice(payment.amount_sat)
            logger.info(f"WapuPay invoice created: {invoice}")
            bolt11 = invoice.get("lnurl_pr_invoice") or invoice.get("payment_request") or invoice.get("bolt11")
            return {
                "bolt11": bolt11,
                "amount_sat": payment.amount_sat,
                "invoice_source": "wapu",
                "ars_quote": quote,
                "threshold_info": {
                    "will_convert_sat": convert_sat,
                    "will_keep_sat": keep_sat,
                    "message": f"Converting all {convert_sat} sats to ARS via WapuPay."
                },
                "wapu_response": invoice
            }
        except Exception as e:
            logger.error(f"Failed to create WapuPay invoice: {e}")
            raise HTTPException(status_code=502, detail=f"WapuPay error: {str(e)}")


class RecordPaymentRequest(BaseModel):
    transaction_id: str
    amount_sat: float


@app.post("/record-payment")
async def record_payment(body: RecordPaymentRequest):
    """
    Called by the checkout page the moment WapuPay confirms a payment.
    Fetches the real ARS amount from WapuPay, then records everything
    into the threshold tracker so the plugin dashboard updates.
    Ignores duplicates — safe to call multiple times for the same transaction.
    """
    status = get_status()
    already_recorded = any(
        p.get("invoice_id") == body.transaction_id
        for p in status.get("payments", [])
    )
    if already_recorded:
        return {"status": "already_recorded", "threshold": status}

    # Fetch real ARS amount from WapuPay — only record it if WapuPay has completed the conversion
    ars_amount = 0
    try:
        tx = await get_transaction(body.transaction_id)
        tx_status = tx.get("status", "").upper()
        is_completed = tx_status in ("COMPLETED", "DONE", "PAID", "SUCCESS")
        if is_completed:
            ars_amount = float(tx.get("payment_amount") or tx.get("ars_amount") or 0)
        logger.info(f"WapuPay transaction {body.transaction_id}: status={tx_status}, {body.amount_sat} sats → {ars_amount} ARS")
    except Exception as e:
        logger.warning(f"Could not fetch ARS amount for {body.transaction_id}: {e}")

    will_convert, convert_sat, keep_sat = should_convert(body.amount_sat)
    if convert_sat > 0:
        record_conversion(convert_sat, ars_amount, body.transaction_id)
    if keep_sat > 0:
        record_kept(keep_sat, body.transaction_id)

    logger.info(f"Recorded WapuPay payment {body.transaction_id}: {convert_sat} sat converted → {ars_amount} ARS, {keep_sat} sat kept")
    return {
        "status": "recorded",
        "converted_sat": convert_sat,
        "kept_sat": keep_sat,
        "ars_amount": ars_amount,
        "threshold": get_status()
    }


@app.get("/check-lnd-payment/{r_hash}")
async def check_lnd_payment(r_hash: str, convert_sat: float = 0, keep_sat: float = 0):
    """
    Poll LND for invoice settlement status.
    When paid, triggers the split: sends convert_sat to WapuPay, keeps keep_sat in LND.
    """
    try:
        invoice = await get_lnd_invoice(r_hash)
        settled = invoice.get("settled", False)

        if settled and convert_sat > 0:
            # Payment landed in LND — record the split
            # Note: on staging, LND→WapuPay auto-payment may fail due to node topology.
            # In production this would trigger an outbound payment to WapuPay.
            try:
                wapu_invoice = await create_lightning_invoice(convert_sat)
                wapu_bolt11 = (
                    wapu_invoice.get("lnurl_pr_invoice") or
                    wapu_invoice.get("payment_request") or
                    wapu_invoice.get("bolt11")
                )
                await pay_lnd_invoice(wapu_bolt11)
                logger.info(f"LND→WapuPay payment sent for {convert_sat} sats")
            except Exception as e:
                logger.warning(f"LND→WapuPay auto-payment skipped (staging limitation): {e}")

            # Always record the split regardless of payment success
            record_conversion(convert_sat, 0, r_hash)
            if keep_sat > 0:
                record_kept(keep_sat, r_hash)
            logger.info(f"Split recorded: {convert_sat} sat → ARS conversion, {keep_sat} sat kept in Lightning")

        return {"paid": settled, "settled": settled, "invoice": invoice}
    except Exception as e:
        logger.warning(f"LND payment check failed for {r_hash}: {e}")
        return {"paid": False, "settled": False, "error": str(e)}


@app.get("/check-payment/{transaction_id}")
async def check_payment(transaction_id: str):
    """
    Poll WapuPay for the status of a Lightning invoice.
    Called every few seconds by the checkout page to detect payment.
    Returns paid=True when WapuPay confirms the payment landed.
    """
    try:
        tx = await get_transaction(transaction_id)
        status = tx.get("status", "").upper()
        # WapuPay marks completed payments as COMPLETED or DONE
        paid = status in ("COMPLETED", "DONE", "PAID", "SUCCESS")
        return {
            "paid": paid,
            "status": status,
            "transaction": tx
        }
    except Exception as e:
        logger.warning(f"Payment check failed for {transaction_id}: {e}")
        return {"paid": False, "status": "ERROR", "error": str(e)}


@app.get("/transactions")
async def transactions():
    """Returns all WapuPay transactions for this account."""
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
    Applies threshold logic: convert to ARS or keep in Lightning.
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

    if event_type != "InvoiceSettled":
        return {"status": "ignored", "event_type": event_type}

    # Get invoice details to find amount
    try:
        invoice = await get_invoice(invoice_id)
        amount_sat = float(invoice.get("amount", 0))
    except Exception as e:
        logger.error(f"Could not fetch invoice: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    # Apply threshold logic
    will_convert, convert_sat, keep_sat = should_convert(amount_sat)

    if not will_convert:
        # Threshold already reached — everything stays in Lightning
        record_kept(amount_sat, invoice_id)
        logger.info(f"Threshold reached — keeping {amount_sat} sats in Lightning")
        return {
            "status": "kept_in_lightning",
            "amount_sat": amount_sat,
            "reason": "Monthly ARS threshold reached"
        }

    # Need to convert convert_sat to ARS via WapuPay.
    # Since sats landed in BTCPay's LND node, we pay a WapuPay invoice from LND.
    ars_amount = 0
    try:
        # Step 1: Create a WapuPay Lightning invoice for the ARS portion
        wapu_invoice = await create_lightning_invoice(convert_sat)
        wapu_bolt11 = (
            wapu_invoice.get("lnurl_pr_invoice") or
            wapu_invoice.get("payment_request") or
            wapu_invoice.get("bolt11")
        )
        logger.info(f"WapuPay invoice created for {convert_sat} sats: {wapu_bolt11[:40]}...")

        # Step 2: Pay the WapuPay invoice from BTCPay's LND node
        pay_result = await pay_lightning_invoice(wapu_bolt11)
        logger.info(f"LND paid WapuPay invoice: {pay_result}")

        # Step 3: Check WapuPay for the ARS amount
        wapu_tx_id = wapu_invoice.get("transaction_id") or wapu_invoice.get("id")
        if wapu_tx_id:
            try:
                tx = await get_transaction(wapu_tx_id)
                ars_amount = float(tx.get("payment_amount") or 0)
            except Exception:
                pass

        record_conversion(convert_sat, ars_amount, invoice_id)
        logger.info(f"Converted {convert_sat} sats → {ars_amount} ARS")

        if keep_sat > 0:
            record_kept(keep_sat, invoice_id)
            logger.info(f"Kept {keep_sat} sats in Lightning node")

        return {
            "status": "success",
            "converted_sat": convert_sat,
            "kept_sat": keep_sat,
            "ars_amount": ars_amount
        }
    except Exception as e:
        logger.error(f"LND → WapuPay payment failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
