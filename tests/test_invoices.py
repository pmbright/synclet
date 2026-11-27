"""Invoice behaviour captured before removing the obsolete application paths."""
from contextlib import redirect_stdout
from datetime import datetime
from decimal import Decimal
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

with patch.dict(os.environ, {"QBO_CLIENT_ID": "test-client", "QBO_CLIENT_SECRET": "test-secret"}):
    from qbo_exporter import QBOExporter

import requests


def order():
    raw = {
        "Items": [{"ProductCode": "BB-1", "ProductName": "Suit", "Quantity": "2",
                   "UnitPriceExTax": "20.00", "Discount": "1.20",
                   "Taxes": {"TaxRate": "0.20"}, "LineTotalIncTax": "45.60"}],
        "Shipping": {"Amount": "6.00", "Taxes": {"TaxAmount": "1.00", "TaxRate": "0.20"}},
        "Addresses": {"ShippingAddress": {"CountryCode": "GB"},
                      "BillingAddress": {"Line1": "Test street", "CountryCode": "GB"}},
        "Contact": {"Email": "test@example.invalid"}, "Total": "51.60", "Discounts": "2.40",
    }
    return {"order_number": "000123", "order_date": datetime(2026, 1, 2), "raw_json": json.dumps(raw)}


class InvoiceTests(unittest.TestCase):
    def setUp(self):
        # Any unmocked HTTP or DB connection is a test failure, never a live call.
        guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network call"))
        guard.start()
        self.addCleanup(guard.stop)
        self.connection = Mock()
        self.exporter = QBOExporter(self.connection, {"qbo": {"base_url": "https://example.invalid",
                                                           "customer_id": "customer"}})
        self.exporter._get_item_id_by_name = Mock(return_value="product")
        token = patch("qbo_exporter.get_access_token", return_value="test-token")
        realm = patch("qbo_exporter.get_realm_id", return_value="123")
        token.start()
        realm.start()
        self.addCleanup(token.stop)
        self.addCleanup(realm.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def export(self, record, invoice_total="51.60", post_responses=None):
        invoice = {"Id": "invoice-1", "SyncToken": "1", "TotalAmt": invoice_total, "Line": []}
        self.exporter._get_invoice = Mock(return_value=invoice)
        response = Mock()
        response.json.return_value = {"Invoice": invoice}
        with patch("qbo_exporter.requests.post", side_effect=post_responses or [response]) as post:
            self.exporter._export_order(record, "zero", "standard", "shipping", "adjustment")
        return [json.loads(call.kwargs["data"]) for call in post.call_args_list]

    def test_taxed_discount_shipping_and_invoice_identity(self):
        payload, = self.export(order())
        self.assertEqual(payload["DocNumber"], "M000123")
        self.assertEqual(payload["TxnDate"], "2026-01-02")
        self.assertEqual(payload["CustomerRef"], {"value": "customer"})
        self.assertEqual(payload["GlobalTaxCalculation"], "TaxExcluded")
        self.assertEqual(payload["BillEmail"], {"Address": "test@example.invalid"})
        self.assertEqual(payload["BillAddr"]["Line1"], "Test street")
        self.assertEqual(payload["Line"][0], {
            "DetailType": "SalesItemLineDetail", "Amount": 38.0, "Description": "Suit",
            "SalesItemLineDetail": {"ItemRef": {"value": "product"}, "Qty": 2,
                                    "UnitPrice": 19.0, "TaxCodeRef": {"value": "standard"}}})
        self.assertEqual(payload["Line"][1]["Amount"], 5.0)
        self.assertEqual(payload["Line"][1]["SalesItemLineDetail"]["ItemRef"]["value"], "shipping")
        self.connection.commit.assert_called_once()
        params = self.connection.cursor.return_value.execute.call_args.args[1]
        self.assertEqual(params, ("000123", "invoice-1", False, 0, "success"))

    def test_zero_rated_items_and_shipping(self):
        record = order()
        raw = json.loads(record["raw_json"])
        raw["Items"][0]["Taxes"]["TaxRate"] = "0"
        raw["Shipping"]["Taxes"] = {"TaxRate": "0", "TaxAmount": "0"}
        raw["Total"] = "43.60"
        record["raw_json"] = json.dumps(raw)
        payload, = self.export(record, "43.60")
        self.assertEqual(payload["Line"][0]["Amount"], 37.6)
        self.assertEqual(payload["Line"][1]["Amount"], 6.0)
        self.assertTrue(all(line["SalesItemLineDetail"]["TaxCodeRef"]["value"] == "zero"
                            for line in payload["Line"]))

    def test_duplicate_product_lines_merge(self):
        record = order()
        raw = json.loads(record["raw_json"])
        raw["Items"][0]["Quantity"] = "1"
        raw["Items"].append(dict(raw["Items"][0]))
        record["raw_json"] = json.dumps(raw)
        payload, = self.export(record)
        self.assertEqual(len(payload["Line"]), 2)
        self.assertEqual(payload["Line"][0]["SalesItemLineDetail"]["Qty"], 2)
        self.assertEqual(payload["Line"][0]["Amount"], 38.0)

    def test_rounding_difference_creates_adjustment_line(self):
        record = order()
        raw = json.loads(record["raw_json"])
        raw["Total"] = "51.61"
        record["raw_json"] = json.dumps(raw)
        response = Mock()
        response.json.return_value = {"Invoice": {"Id": "invoice-1", "SyncToken": "1", "TotalAmt": "51.61", "Line": []}}
        create, update = self.export(record, post_responses=[response, response])
        self.assertEqual(update["Line"][-1]["Amount"], 0.01)
        self.assertEqual(update["Line"][-1]["SalesItemLineDetail"]["ItemRef"]["value"], "adjustment")
        self.assertEqual(self.connection.cursor.return_value.execute.call_args.args[1][2:4], (True, 0.01))

    def test_failed_creation_never_records_export_success(self):
        response = Mock()
        response.ok = False
        response.raise_for_status.side_effect = requests.HTTPError("test failure")
        with self.assertRaises(requests.HTTPError):
            self.export(order(), post_responses=[response])
        self.connection.commit.assert_not_called()
        self.connection.cursor.assert_not_called()

    def test_historical_csv_and_qbo_exports_remain_excluded(self):
        self.exporter.config["qbo"].update(vat_0_name="zero", vat_20_name="standard",
                                           shipping_item_name="shipping", adjustment_item_name="adjustment")
        self.connection.cursor.return_value.fetchall.return_value = []
        self.exporter._get_tax_code_id_by_name = Mock(return_value="tax")
        self.exporter.export_orders()
        query = self.connection.cursor.return_value.execute.call_args.args[0]
        self.assertIn("LEFT JOIN csv_export_history", query)
        self.assertIn("LEFT JOIN qbo_export_history", query)
        self.assertIn("o.status IN ('complete', 'processing')", query)

    def test_existing_product_code_corrections(self):
        exporter = QBOExporter(self.connection, {})
        exporter._qbo_query = Mock(return_value={"Item": [{"Id": "product"}]})
        for old, new in [("BBUV1", "BBUV1-Large"), ("BBU7TV-BBTVS", "BBU7TV"),
                         ("BB7TV-Rose-BBTVS", "BBU7TV-Rose")]:
            self.assertEqual(exporter._get_item_id_by_name(old), "product")
            self.assertIn(f"Name = '{new}'", exporter._qbo_query.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
