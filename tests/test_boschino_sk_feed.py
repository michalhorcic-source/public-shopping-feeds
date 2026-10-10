import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_boschino_sk_feed.py"
FIXTURE = ROOT / "tests" / "fixtures" / "boschino-sk-products.json"
FIELDS = ["item_id", "title", "description", "url", "brand", "seller_name", "image_url", "availability", "price", "is_ads_eligible", "sale_price", "gtin", "mpn", "product_types", "custom_label_1", "custom_label_2", "custom_label_3", "custom_label_4"]
SPEC = importlib.util.spec_from_file_location("boschino_sk_builder", BUILDER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

class BoschinoSkFeedTest(unittest.TestCase):
    def test_fixture_produces_approved_eur_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "feed.csv"; state = Path(directory) / "state.json"
            result = subprocess.run([sys.executable, str(BUILDER), "--offline-products", str(FIXTURE), "--output", str(output), "--state", str(state)], capture_output=True, text=True, check=True)
            self.assertIn("Validated 1 eligible SK Merchant offers", result.stdout); self.assertFalse(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            with output.open(encoding="utf-8", newline="") as handle: reader = csv.DictReader(handle); rows = list(reader)
            self.assertEqual(FIELDS, reader.fieldnames); self.assertEqual(1, len(rows)); self.assertEqual("SKU-SK-1", rows[0]["item_id"]); self.assertEqual("1234.00 EUR", rows[0]["price"]); self.assertEqual("999.00 EUR", rows[0]["sale_price"]); self.assertEqual("Boschino.sk", rows[0]["seller_name"]); self.assertEqual("Náhradné diely|Práčky", rows[0]["product_types"]); self.assertEqual("seasonal", rows[0]["custom_label_1"]); self.assertEqual("priority-b", rows[0]["custom_label_2"]); self.assertEqual("warehouse-sk", rows[0]["custom_label_3"]); self.assertEqual("margin-medium", rows[0]["custom_label_4"])

    def test_missing_optional_merchant_taxonomy_and_labels_are_blank(self):
        product = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
        for name in ("productTypes", "customLabel1", "customLabel2", "customLabel3", "customLabel4"): product["productAttributes"].pop(name)
        row = MODULE.transform(product)
        self.assertEqual("", row["product_types"])
        self.assertEqual(["", "", "", ""], [row[f"custom_label_{index}"] for index in range(1, 5)])

    def test_wrong_country_becomes_tombstone(self):
        eligible = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]; products = []
        for index in range(1, 5):
            product = json.loads(json.dumps(eligible)); product["offerId"] = f"SKU-SK-{index}"; products.append(product)
        removed = json.loads(json.dumps(eligible)); removed["offerId"] = "SKU-SK-5"; removed["productStatus"]["destinationStatuses"][0]["approvedCountries"] = ["CZ"]; products.append(removed)
        rows, state = MODULE.build(products, {"active_item_ids": [f"SKU-SK-{index}" for index in range(1, 6)], "tombstones": {}})
        self.assertEqual([f"SKU-SK-{index}" for index in range(1, 5)], [row["item_id"] for row in rows]); self.assertEqual("not_shopping_ads_approved_for_SK", state["tombstones"]["SKU-SK-5"]["reason"])

if __name__ == "__main__": unittest.main()
