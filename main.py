"""
SatTope — Lightning payment layer for Argentine merchants.

Flow:
1. Customer visits the webstore and picks a product
2. At checkout, SatTope checks the monthly conversion tope
3. Below tope → WapuPay generates the invoice, sats auto-convert to ARS
4. Above tope → LND node generates the invoice, sats stay in Lightning
5. Dashboard shows conversion history, threshold status, and WapuPay transactions
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from lnd import get_lnd_balance, pay_lnd_invoice
from phoenixd import create_phoenixd_invoice, get_phoenixd_balance, get_phoenixd_invoice
from threshold import get_status, record_conversion, record_kept, should_convert, update_threshold
from wapu import create_lightning_invoice, get_lightning_address, get_quote, get_transaction, get_transactions

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SatTope",
    description="Lightning payment layer for Argentine merchants — auto-converts sats to ARS below your monthly tope, stacks sats above it.",
    version="1.0.0"
)

# Serve static files (CSS) and HTML templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- Request models ---

class PaymentRequest(BaseModel):
    amount_sat: float
    description: str = "Payment"


class ThresholdUpdate(BaseModel):
    threshold_sat: int


class RecordPaymentRequest(BaseModel):
    transaction_id: str
    amount_sat: float


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
    """Payment confirmation page."""
    return templates.TemplateResponse(request, "confirmation.html")


@app.get("/plugin", response_class=HTMLResponse)
async def plugin_page(request: Request):
    """SatTope merchant dashboard."""
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
    """Returns current month's conversion threshold status."""
    return get_status()


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
    Aggregates data for the SatTope merchant dashboard.
    Returns Lightning balance, WapuPay transactions, threshold status, and payment history.
    """
    # Lightning node balance (phoenixd)
    try:
        lightning_balance = await get_phoenixd_balance()
    except Exception:
        lightning_balance = {}

    # WapuPay transactions
    try:
        wapu_txs = await get_transactions()
        if isinstance(wapu_txs, dict):
            wapu_txs = wapu_txs.get("transactions", []) or wapu_txs.get("items", []) or []
    except Exception:
        wapu_txs = []

    # Threshold status
    threshold = get_status()

    # Build WapuPay lookup for cross-referencing ARS amounts
    wapu_tx_lookup = {}
    for tx in wapu_txs:
        tx_id = tx.get("transaction_id") or tx.get("id")
        if tx_id:
            wapu_tx_lookup[str(tx_id)] = tx

    # ARS payouts enriched with live WapuPay data.
    # wapu_status is ONLY set from WapuPay's actual API response — never inferred locally.
    # If WapuPay hasn't confirmed the transaction yet, status stays PENDING.
    ars_payouts = []
    for p in threshold.get("payments", []):
        tx_id = str(p.get("invoice_id", ""))
        payment_type = p.get("type", "converted")

        # kept_in_lightning payments don't go through WapuPay — mark them directly
        if payment_type == "kept_in_lightning":
            ars_payouts.append({
                "date": p.get("date", ""),
                "invoice_id": tx_id,
                "amount_sat": p.get("amount_sat", 0),
                "ars_amount": 0,
                "type": payment_type,
                "wapu_status": "KEPT"
            })
            continue

        # For converted payments, only trust WapuPay's live status
        wapu_tx = wapu_tx_lookup.get(tx_id, {})
        wapu_status = wapu_tx.get("status", "").upper() if wapu_tx else "PENDING"
        is_completed = wapu_status in ("COMPLETED", "DONE", "PAID", "SUCCESS")
        ars_amount = float(wapu_tx.get("payment_amount") or 0) if is_completed else 0

        logger.info(f"ARS payout {tx_id[:16]}: wapu_found={bool(wapu_tx)}, wapu_status={wapu_status}, ars={ars_amount}")

        ars_payouts.append({
            "date": p.get("date", ""),
            "invoice_id": tx_id,
            "amount_sat": p.get("amount_sat", 0),
            "ars_amount": ars_amount,
            "type": payment_type,
            "wapu_status": wapu_status
        })

    # Lightning payments list (all threshold-tracked payments)
    lightning_payments = [
        {
            "id": p.get("invoice_id", ""),
            "date": p.get("date", ""),
            "amount_sat": p.get("amount_sat", 0),
            "type": p.get("type", "converted"),
            "status": "Settled"
        }
        for p in threshold.get("payments", [])
    ]

    return {
        "store_name": "BTC Hardware Solutions",
        "lightning_balance": lightning_balance,
        "lightning_payments": lightning_payments,
        "ars_payouts": ars_payouts,
        "threshold": threshold,
        "wapu_transactions": wapu_txs
    }


@app.post("/create-payment")
async def create_payment(payment: PaymentRequest):
    """
    Creates a Lightning invoice based on the monthly tope.

    Below tope: WapuPay generates the invoice → sats auto-convert to ARS.
    Above tope: LND node generates the invoice → sats stay in Lightning wallet.
    Split: LND generates the invoice; on settlement, the ARS portion is sent to WapuPay.
    """
    logger.info(f"Creating payment for {payment.amount_sat} sats")

    will_convert, convert_sat, keep_sat = should_convert(payment.amount_sat)
    use_lnd = keep_sat > 0  # True if any sats would be kept

    # Get ARS quote for display
    try:
        quote = await get_quote(min(convert_sat, payment.amount_sat) if convert_sat > 0 else payment.amount_sat)
    except Exception as e:
        logger.warning(f"Could not get quote: {e}")
        quote = {}

    if use_lnd:
        # Above tope — phoenixd generates the invoice, sats land in merchant's wallet
        try:
            invoice = await create_phoenixd_invoice(int(payment.amount_sat), payment.description)
            logger.info(f"phoenixd invoice created: {invoice}")
            bolt11 = invoice.get("serialized")
            payment_hash = invoice.get("paymentHash", "")
            return {
                "bolt11": bolt11,
                "amount_sat": payment.amount_sat,
                "invoice_source": "phoenixd",
                "r_hash": payment_hash,
                "convert_sat": convert_sat,
                "keep_sat": keep_sat,
                "ars_quote": quote,
                "threshold_info": {
                    "will_convert_sat": convert_sat,
                    "will_keep_sat": keep_sat,
                    "message": f"phoenixd receives payment. Converting {convert_sat} sats to ARS, keeping {keep_sat} sats in Lightning."
                }
            }
        except Exception as e:
            logger.error(f"Failed to create phoenixd invoice: {e}")
            raise HTTPException(status_code=502, detail=f"phoenixd error: {str(e)}")
    else:
        # Below tope — WapuPay generates the invoice → auto-converts to ARS
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


@app.post("/record-payment")
async def record_payment(body: RecordPaymentRequest):
    """
    Called by the checkout page when WapuPay confirms a payment.
    Fetches the real ARS amount from WapuPay, records into threshold tracker.
    Ignores duplicates — safe to call multiple times for the same transaction.
    """
    status = get_status()
    already_recorded = any(
        p.get("invoice_id") == body.transaction_id
        for p in status.get("payments", [])
    )
    if already_recorded:
        return {"status": "already_recorded", "threshold": status}

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

    logger.info(f"Recorded payment {body.transaction_id}: {convert_sat} sat → {ars_amount} ARS, {keep_sat} sat kept")
    return {
        "status": "recorded",
        "converted_sat": convert_sat,
        "kept_sat": keep_sat,
        "ars_amount": ars_amount,
        "threshold": get_status()
    }


@app.post("/reset-demo")
async def reset_demo():
    """
    Resets the threshold tracker to zero for demo purposes.
    Keeps the threshold_sat setting but clears converted_sat and payment history.
    """
    from threshold import _load, _save
    data = _load()
    threshold_sat = data.get("threshold_sat", 10000)
    fresh = {
        "month": data["month"],
        "threshold_sat": threshold_sat,
        "converted_sat": 0,
        "payments": []
    }
    _save(fresh)
    logger.info("Demo reset: threshold cleared")
    return {"status": "reset", "threshold_sat": threshold_sat}


@app.get("/check-lnd-payment/{r_hash}")
async def check_lnd_payment(r_hash: str, convert_sat: float = 0, keep_sat: float = 0):
    """
    Poll phoenixd for invoice settlement.
    When paid, records the split and triggers ARS conversion for the convert_sat portion.
    """
    try:
        invoice = await get_phoenixd_invoice(r_hash)
        settled = invoice.get("isPaid", False)

        if settled and convert_sat > 0:
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
                logger.warning(f"LND→WapuPay auto-payment skipped: {e}")

            record_conversion(convert_sat, 0, r_hash)
            if keep_sat > 0:
                record_kept(keep_sat, r_hash)
            logger.info(f"Split recorded: {convert_sat} sat → ARS, {keep_sat} sat kept")

        return {"paid": settled, "settled": settled, "invoice": invoice}
    except Exception as e:
        logger.warning(f"LND payment check failed for {r_hash}: {e}")
        return {"paid": False, "settled": False, "error": str(e)}


@app.get("/check-payment/{transaction_id}")
async def check_payment(transaction_id: str):
    """
    Poll WapuPay for the status of a Lightning invoice.
    Called every few seconds by the checkout page to detect payment.
    """
    try:
        tx = await get_transaction(transaction_id)
        status = tx.get("status", "").upper()
        paid = status in ("COMPLETED", "DONE", "PAID", "SUCCESS")
        return {"paid": paid, "status": status, "transaction": tx}
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
