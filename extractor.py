#!/usr/bin/env python3
import json
import os
import sys

import requests
from dotenv import load_dotenv

# Load WHAPI_TOKEN from .env
load_dotenv()

BASE_URL = "https://gate.whapi.cloud"
INPUT_FILE = "input.json"
OUTPUT_FILE = "output.json"


def log(msg: str, level: str = "INFO"):
    """Single output stream — no stderr interleaving."""
    prefix = {
        "INFO":  "      ",
        "OK":    "  [OK]",
        "SKIP":  "[SKIP]",
        "FETCH": "[FETCH]",
        "WARN":  " [WARN]",
        "ERROR": "[ERROR]",
    }.get(level, "      ")
    print(f"{prefix} {msg}", flush=True)


def get_catalog(contact_id: str, token: str, count: int = 100, offset: int = 0):
    url = f"{BASE_URL}/business/{contact_id}/products"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    params = {
        "count": count,
        "offset": offset,
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)

    # Try to parse JSON whether the request succeeded or failed.
    try:
        data = response.json()
    except ValueError:
        data = response.text

    if not response.ok:
        raise requests.HTTPError(
            f"HTTP {response.status_code}: {data}",
            response=response,
        )

    return data


def load_json_file(path: str, default):
    """Load a JSON file, returning `default` if it doesn't exist or is empty."""
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return default
        return json.loads(content)


def save_json_file(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def make_output_key(glid: str, phone: str) -> str:
    """Build the output key: <glid>_<phone>"""
    return f"{glid}_{phone}"


def build_contact_id(phone: str) -> tuple[str, str | None]:
    """
    Build the WhatsApp contact_id from a raw phone number.

    Rules:
    - Strip leading zeros (e.g. 02602680034 → 2602680034)
    - If the cleaned number is exactly 10 digits, treat as Indian → prepend 91
    - If it already looks like it has a country code (>10 digits), use as-is
    - If it's <10 digits (landline fragment, etc.), flag as likely invalid

    Returns:
        (contact_id, warning_msg)  — warning_msg is None if all looks fine
    """
    cleaned = phone.lstrip("0")

    if len(cleaned) == 10:
        # Standard Indian 10-digit mobile
        return f"91{cleaned}", None
    elif len(cleaned) > 10:
        # Already has a country code embedded — use as-is
        return cleaned, None
    else:
        # Suspiciously short — likely a landline or fragment
        return f"91{cleaned}", f"Phone '{phone}' is only {len(cleaned)} digit(s) after stripping leading zeros — may be a landline or invalid"


def main():
    token = os.getenv("WHAPI_TOKEN")
    if not token:
        log("WHAPI_TOKEN not set. Add it to your .env file.", "ERROR")
        sys.exit(1)

    # Load inputs
    businesses = load_json_file(INPUT_FILE, {})
    if not businesses:
        log(f"No businesses found in {INPUT_FILE}.", "ERROR")
        sys.exit(1)

    results = load_json_file(OUTPUT_FILE, {})

    skipped = 0
    fetched = 0
    failed_total = 0

    for glid, biz_info in businesses.items():
        business_name = biz_info.get("business_name", glid)
        phone_numbers = biz_info.get("phone_numbers", [])

        if not phone_numbers:
            log(f"{business_name!r} (glid={glid}) has no phone numbers — skipping.", "SKIP")
            skipped += 1
            continue

        for phone in phone_numbers:
            output_key = make_output_key(glid, phone)
            contact_id, warn = build_contact_id(phone)

            # Skip if already successfully fetched (no pending retry)
            if output_key in results and results[output_key].get("__failed__") is not True:
                log(f"{output_key} ({business_name}) already in results.", "SKIP")
                skipped += 1
                continue

            if warn:
                log(warn, "WARN")

            print(f"[FETCH] {business_name!r} | {output_key} ({contact_id}) ... ", end="", flush=True)
            try:
                data = get_catalog(contact_id=contact_id, token=token)
                product_count = len(data.get("products", []))
                print(f"OK ({product_count} products)")

                # Store the result under glid_phone key, include metadata
                results[output_key] = {
                    "glid": glid,
                    "business_name": business_name,
                    "phone": phone,
                    "products": data.get("products", []),
                    "count": data.get("count", 100),
                    "total": data.get("total", product_count),
                    "offset": data.get("offset", 0),
                }
                save_json_file(OUTPUT_FILE, results)
                fetched += 1
                log(f"Saved {product_count} products for {output_key}.", "OK")

            except requests.HTTPError as e:
                print("FAILED")
                log(f"HTTP error for {output_key} ({contact_id}): {e}", "ERROR")
                # Mark as failed so next run retries it
                results[output_key] = {
                    "glid": glid,
                    "business_name": business_name,
                    "phone": phone,
                    "__failed__": True,
                    "error": str(e),
                }
                save_json_file(OUTPUT_FILE, results)
                failed_total += 1

            except requests.RequestException as e:
                print("FAILED")
                log(f"Request error for {output_key} ({contact_id}): {e}", "ERROR")
                results[output_key] = {
                    "glid": glid,
                    "business_name": business_name,
                    "phone": phone,
                    "__failed__": True,
                    "error": str(e),
                }
                save_json_file(OUTPUT_FILE, results)
                failed_total += 1

    print(
        f"\nDone. Fetched: {fetched}, Skipped: {skipped}, "
        f"Failed: {failed_total}. Results saved to {OUTPUT_FILE}."
    )


if __name__ == "__main__":
    main()