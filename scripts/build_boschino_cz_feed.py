#!/usr/bin/env python3
"""Build the Boschino CZ ChatGPT Ads feed from Merchant API products.list."""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

FIELDS = ["item_id", "title", "description", "url", "brand", "seller_name", "image_url", "availability", "price", "is_ads_eligible", "sale_price", "gtin", "mpn", "product_types", "custom_label_1", "custom_label_2", "custom_label_3", "custom_label_4"]
ACCOUNT = "5757276720"
DATA_SOURCE = "accounts/5757276720/dataSources/10714978275"
API_URL = f"https://merchantapi.googleapis.com/products/v1/accounts/{ACCOUNT}/products"
AVAILABILITY = {"IN_STOCK": "in_stock", "OUT_OF_STOCK": "out_of_stock", "PREORDER": "pre_order", "BACKORDER": "backorder"}
BAD_TEXT = (chr(0xFFFD), chr(0x00C3), chr(0x0102), chr(0x00E2) + chr(0x20AC))


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()


def value(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def money(raw: Any, required: bool = False) -> str:
    if raw in (None, ""):
        if required:
            raise ValueError("missing price")
        return ""
    if isinstance(raw, dict):
        currency = clean(value(raw, "currencyCode", "currency_code", "currency"))
        amount = value(raw, "amount", "amountMicros", "amount_micros", "value")
        if "amountMicros" in raw or "amount_micros" in raw:
            try:
                amount = Decimal(str(amount)) / Decimal("1000000")
            except InvalidOperation as exc:
                raise ValueError("invalid price amountMicros") from exc
    else:
        match = re.fullmatch(r"\s*([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-z]{3})\s*", str(raw))
        if not match:
            raise ValueError("invalid price")
        amount, currency = match.groups()
    if currency.upper() != "CZK":
        raise ValueError("price is not CZK")
    try:
        decimal = Decimal(str(amount).replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("invalid price amount") from exc
    if decimal <= 0:
        raise ValueError("non-positive price")
    return f"{decimal.quantize(Decimal('0.01'))} CZK"


def valid_gtin(raw: Any) -> str:
    code = re.sub(r"\D", "", clean(raw))
    if len(code) not in {8, 12, 13, 14}:
        return ""
    check = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(code[:-1])))
    return code if (10 - check % 10) % 10 == int(code[-1]) else ""


def product_types(raw: Any) -> str:
    values = raw if isinstance(raw, list) else [raw]
    return "|".join(item for item in (clean(value) for value in values) if item)


def is_shopping_ads_cz(product: dict[str, Any]) -> bool:
    status = value(product, "productStatus", "product_status") or {}
    destinations = value(status, "destinationStatuses", "destination_statuses") or []
    return any(clean(value(destination, "reportingContext", "reporting_context")).upper() == "SHOPPING_ADS" and "CZ" in {clean(country).upper() for country in value(destination, "approvedCountries", "approved_countries") or []} for destination in destinations if isinstance(destination, dict))


def transform(product: dict[str, Any]) -> dict[str, str]:
    attributes = value(product, "productAttributes", "product_attributes", "attributes") or {}
    item_id = clean(value(product, "offerId", "offer_id")); title = clean(value(attributes, "title")); description = clean(value(attributes, "description")); url = clean(value(attributes, "link")); brand = clean(value(attributes, "brand")); image_url = clean(value(attributes, "imageLink", "image_link")); availability = clean(value(attributes, "availability")).upper()
    if not all((item_id, title, description, url, brand, image_url)):
        raise ValueError("missing required offer field")
    if availability not in AVAILABILITY:
        raise ValueError("unsupported availability")
    for label, candidate in (("url", url), ("image_url", image_url)):
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{label} is not HTTPS")
    price = money(value(attributes, "price"), required=True)
    sale_price = money(value(attributes, "salePrice", "sale_price"))
    if sale_price and Decimal(sale_price.split()[0]) >= Decimal(price.split()[0]):
        sale_price = ""
    gtin_values = value(attributes, "gtins", "gtin") or []
    if not isinstance(gtin_values, list):
        gtin_values = [gtin_values]
    gtin = next((candidate for candidate in (valid_gtin(item) for item in gtin_values) if candidate), "")
    return {"item_id": item_id, "title": title, "description": description, "url": url, "brand": brand, "seller_name": "Boschino.cz", "image_url": image_url, "availability": AVAILABILITY[availability], "price": price, "is_ads_eligible": "true", "sale_price": sale_price, "gtin": gtin, "mpn": clean(value(attributes, "mpn")), "product_types": product_types(value(attributes, "productTypes", "product_types")), "custom_label_1": clean(value(attributes, "customLabel1", "custom_label_1")), "custom_label_2": clean(value(attributes, "customLabel2", "custom_label_2")), "custom_label_3": clean(value(attributes, "customLabel3", "custom_label_3")), "custom_label_4": clean(value(attributes, "customLabel4", "custom_label_4"))}


def fetch_products(token: str) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []; page_token = ""; seen_tokens: set[str] = set()
    while True:
        query = {"pageSize": "1000"}
        if page_token: query["pageToken"] = page_token
        request = Request(API_URL + "?" + urlencode(query), headers={"Authorization": f"Bearer {token}"})
        try:
            with urlopen(request, timeout=120) as response: payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("Merchant API products.list request failed") from exc
        batch = payload.get("products")
        if not isinstance(batch, list): raise RuntimeError("Merchant API response has no products list")
        products.extend(product for product in batch if isinstance(product, dict)); page_token = clean(payload.get("nextPageToken"))
        if not page_token: break
        if page_token in seen_tokens: raise RuntimeError("Merchant API pagination repeated a page token")
        seen_tokens.add(page_token)
    if not products: raise RuntimeError("refusing empty Merchant API products.list result")
    return products


def load_token(encoded_service_account: str) -> str:
    try:
        info = json.loads(base64.b64decode(encoded_service_account, validate=True))
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleRequest
        credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/content"]); credentials.refresh(GoogleRequest())
    except Exception as exc:
        raise RuntimeError("could not obtain a Merchant API OAuth token from service account secret") from exc
    if not credentials.token: raise RuntimeError("service account did not return an OAuth token")
    return credentials.token


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists(): return {"active_item_ids": [], "tombstones": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state.get("active_item_ids", []), list) or not isinstance(state.get("tombstones", {}), dict): raise RuntimeError("invalid feed state")
    return state


def validate_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    result = list(rows)
    if not result: raise RuntimeError("refusing empty eligible feed")
    if len({row["item_id"] for row in result}) != len(result): raise RuntimeError("duplicate item_id after transformation")
    for row in result:
        if list(row) != FIELDS or any(marker in field for marker in BAD_TEXT for field in row.values()): raise RuntimeError("invalid output row text or schema")
    return result


def build(products: list[dict[str, Any]], prior_state: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    products = [product for product in products if clean(value(product, "dataSource", "data_source")) == DATA_SOURCE]
    if not products: raise RuntimeError("refusing empty result for required Merchant API data source")
    all_ids = {clean(value(product, "offerId", "offer_id")) for product in products}; all_ids.discard(""); rows: list[dict[str, str]] = []; rejected: dict[str, str] = {}
    for product in products:
        item_id = clean(value(product, "offerId", "offer_id"))
        if not is_shopping_ads_cz(product):
            if item_id: rejected[item_id] = "not_shopping_ads_approved_for_CZ"
            continue
        try: rows.append(transform(product))
        except ValueError as exc:
            if item_id: rejected[item_id] = str(exc)
    rows = validate_rows(sorted(rows, key=lambda row: row["item_id"])); active = {row["item_id"] for row in rows}; previous = {clean(item) for item in prior_state.get("active_item_ids", []) if clean(item)}
    if previous and len(active) < len(previous) * 0.7: raise RuntimeError(f"refusing active-item drop over 30%: {len(previous)} -> {len(active)}")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"); tombstones = dict(prior_state.get("tombstones", {}))
    for item_id in previous - active:
        reason = rejected.get(item_id, "removed_from_products_list" if item_id not in all_ids else "incomplete_offer")
        tombstones[item_id] = {"reason": reason, "first_seen": tombstones.get(item_id, {}).get("first_seen", now), "last_seen": now}
    for item_id in active: tombstones.pop(item_id, None)
    return rows, {"active_item_ids": sorted(active), "tombstones": tombstones, "updated_at": now}


def write_utf8(path: Path, content: str) -> None:
    if any(marker in content for marker in BAD_TEXT): raise RuntimeError("refusing corrupted UTF-8 output")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle: handle.write(content); temp_path = Path(handle.name)
    if temp_path.read_bytes().startswith(b"\xef\xbb\xbf"):
        temp_path.unlink(missing_ok=True); raise RuntimeError("refusing UTF-8 BOM output")
    os.replace(temp_path, path)


def write_outputs(rows: list[dict[str, str]], state: dict[str, Any], output: Path, state_path: Path) -> None:
    from io import StringIO
    csv_buffer = StringIO(newline=""); writer = csv.DictWriter(csv_buffer, fieldnames=FIELDS, lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
    write_utf8(output, csv_buffer.getvalue()); write_utf8(state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--state", type=Path, required=True); parser.add_argument("--offline-products", type=Path); args = parser.parse_args(); state = load_state(args.state)
    if args.offline_products: products = json.loads(args.offline_products.read_text(encoding="utf-8"))
    else:
        secret = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "")
        if not secret: raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_B64 is required")
        products = fetch_products(load_token(secret))
    rows, next_state = build(products, state); write_outputs(rows, next_state, args.output, args.state); print(f"Validated {len(rows)} eligible CZ Merchant offers; tombstones={len(next_state['tombstones'])}")


if __name__ == "__main__": main()
