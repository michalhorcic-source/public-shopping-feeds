import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_boschino_cz_feed.py"
FIXTURE = ROOT / "tests" / "fixtures" / "boschino-cz-products.json"
FIELDS = ["item_id", "title", "description", "url", "brand", "seller_name", "image_url", "availability", "price", "is_ads_eligible", "sale_price", "gtin", "mpn"]
SPEC = importlib.util.spec_from_file_location("boschino_builder", BUILDER)
BUILDER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER_MODULE)

class BoschinoCzFeedTest(unittest.TestCase):
    def test_fixture_produces_approved_rfc_csv_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "feed.csv"; state = Path(directory) / "state.json"
            result = subprocess.run([sys.executable, str(BUILDER), "--offline-products", str(FIXTURE), "--output", str(output), "--state", str(state)], capture_output=True, text=True, check=True)
            self.assertIn("Validated 1 eligible CZ Merchant offers", result.stdout); self.assertFalse(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            with output.open(encoding="utf-8", newline="") as handle: reader = csv.DictReader(handle); rows = list(reader); self.assertEqual(FIELDS, reader.fieldnames)
            self.assertEqual(1, len(rows)); self.assertEqual("SKU-1", rows[0]["item_id"]); self.assertEqual("1234.00 CZK", rows[0]["price"]); self.assertEqual("999.00 CZK", rows[0]["sale_price"]); self.assertEqual("4006381333931", rows[0]["gtin"]); self.assertEqual("in_stock", rows[0]["availability"]); self.assertEqual(["SKU-1"], json.loads(state.read_text(encoding="utf-8"))["active_item_ids"])

    def test_newly_ineligible_active_item_becomes_tombstone(self):
        eligible = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
        products = []
        for item_id in ("SKU-1", "SKU-2", "SKU-3"):
            product = json.loads(json.dumps(eligible)); product["offerId"] = item_id; products.append(product)
        removed = json.loads(json.dumps(eligible)); removed["offerId"] = "SKU-4"; removed["productStatus"]["destinationStatuses"][0]["approvedCountries"] = ["SK"]; products.append(removed)
        rows, state = BUILDER_MODULE.build(products, {"active_item_ids": ["SKU-1", "SKU-2", "SKU-3", "SKU-4"], "tombstones": {}})
        self.assertEqual(["SKU-1", "SKU-2", "SKU-3"], [row["item_id"] for row in rows])
        self.assertEqual("not_shopping_ads_approved_for_CZ", state["tombstones"]["SKU-4"]["reason"])

if __name__ == "__main__": unittest.main()
