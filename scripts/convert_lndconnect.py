"""
Convert an lndconnect:// URL to a BTCPay Server connection string.

Usage:
    python3 scripts/convert_lndconnect.py
    (paste your lndconnect:// URL when prompted)
"""

import base64
import urllib.parse


def convert(lndconnect_url: str) -> str:
    parsed = urllib.parse.urlparse(lndconnect_url.strip())
    params = urllib.parse.parse_qs(parsed.query)

    host = parsed.netloc

    macaroon_b64 = params.get("macaroon", [""])[0]
    if not macaroon_b64:
        raise ValueError("No macaroon found in the lndconnect URL")

    # Convert base64url to hex
    macaroon_bytes = base64.urlsafe_b64decode(macaroon_b64 + "==")
    macaroon_hex = macaroon_bytes.hex()

    # If a cert is present, include it — otherwise use allowinsecure (Tor)
    cert_b64 = params.get("cert", [""])[0]
    if cert_b64:
        cert_bytes = base64.urlsafe_b64decode(cert_b64 + "==")
        cert_hex = cert_bytes.hex()
        connection = f"type=lnd-rest;server=https://{host}/;macaroon={macaroon_hex};certthumbprint={cert_hex}"
    else:
        connection = f"type=lnd-rest;server=https://{host}/;macaroon={macaroon_hex};allowinsecure=true"

    return connection


if __name__ == "__main__":
    print("Paste your lndconnect:// URL and press Enter:")
    url = input().strip()
    try:
        result = convert(url)
        print("\nBTCPay connection string:")
        print(result)
    except Exception as e:
        print(f"Error: {e}")
