import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import stock_sync as app


def item(item_id="1", sku="BB-1", qty=3):
    return {"Id": item_id, "Sku": sku, "QtyOnHand": qty, "Type": "Inventory", "Active": True}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.qbo = Mock()
        self.qbo.inventory.return_value = [item()]
        self.magento = Mock()
        self.magento.stock.return_value = {
            "item_id": 10, "manage_stock": True, "qty": 5,
            "min_qty": 0, "backorders": 0, "is_in_stock": True,
        }
        self.report = io.StringIO()

    def run_sync(self, apply=False, skus=None):
        return app.sync(self.qbo, self.magento, self.report, apply=apply, skus=skus)

    def test_default_dry_run_never_writes(self):
        self.assertEqual(self.run_sync(), 0)
        self.magento.update.assert_not_called()
        self.assertIn("would_update", self.report.getvalue())

    def test_bbwear_name_and_sku_match_without_a_mapping(self):
        record = item(sku='BB-101-S')
        record['Name'] = 'BB-101-S'
        self.assertEqual(app.plan_items([record]), ([('1', 'BB-101-S', 3)], 0))

    def test_zero_stock_written_out_of_stock(self):
        self.qbo.inventory.return_value = [item(qty=0)]
        self.assertEqual(self.run_sync(True), 0)
        self.magento.update.assert_called_once_with("BB-1", 10, 0, False)

    def test_sku_filter_updates_only_requested_product(self):
        self.qbo.inventory.return_value = [item(), item('2', 'BB-2')]
        self.assertEqual(self.run_sync(True, skus=['BB-2']), 0)
        self.magento.update.assert_called_once_with('BB-2', 10, 3, True)

    def test_unknown_sku_aborts_before_magento_writes(self):
        with self.assertRaises(app.SyncError):
            self.run_sync(True, skus=['UNKNOWN'])
        self.magento.stock.assert_not_called()

    def test_invalid_boolean_does_not_turn_string_false_into_in_stock(self):
        self.magento.stock.return_value['is_in_stock'] = 'false'
        self.magento.stock.return_value['qty'] = 3
        self.assertEqual(self.run_sync(True), 1)
        self.magento.update.assert_not_called()

    def test_negative_stock_retained_and_backorders_respected(self):
        self.qbo.inventory.return_value = [item(qty=-2)]
        self.magento.stock.return_value["backorders"] = 2
        self.run_sync(True)
        self.magento.update.assert_called_once_with("BB-1", 10, -2, True)

    def test_threshold_controls_availability(self):
        self.magento.stock.return_value["min_qty"] = 4
        self.run_sync(True)
        self.magento.update.assert_called_once_with("BB-1", 10, 3, False)

    def test_duplicate_sku_aborts_before_any_write(self):
        self.qbo.inventory.return_value = [item(), item("2", "BB-1")]
        with self.assertRaises(app.SyncError):
            self.run_sync(True)
        self.magento.stock.assert_not_called()
        self.magento.update.assert_not_called()

    def test_non_finite_quantity_aborts_before_any_write(self):
        self.qbo.inventory.return_value = [item(), item("2", "OTHER", "NaN")]
        with self.assertRaises(app.SyncError):
            self.run_sync(True)
        self.magento.update.assert_not_called()

    def test_unchanged_is_idempotent(self):
        self.magento.stock.return_value["qty"] = 3
        self.assertEqual(self.run_sync(True), 0)
        self.magento.update.assert_not_called()

    def test_status_is_repaired_when_quantity_already_matches(self):
        self.magento.stock.return_value['qty'] = 3
        self.magento.stock.return_value['is_in_stock'] = False
        self.assertEqual(self.run_sync(True), 0)
        self.magento.update.assert_called_once_with('BB-1', 10, 3, True)

    def test_missing_sku_is_not_inferred_from_name(self):
        self.qbo.inventory.return_value = [item(sku=None)]
        self.assertEqual(self.run_sync(True), 1)
        self.magento.stock.assert_not_called()

    def test_unmatched_sku_does_not_prevent_other_updates(self):
        self.qbo.inventory.return_value = [item(), item("2", "BB-2")]
        self.magento.stock.side_effect = [app.ApiError(404), self.magento.stock.return_value]
        self.assertEqual(self.run_sync(True), 1)
        self.magento.update.assert_called_once_with("BB-2", 10, 3, True)

    def test_unmanaged_and_parent_products_are_skipped(self):
        for stock in [None, {"manage_stock": False}]:
            with self.subTest(stock=stock):
                self.magento.stock.return_value = stock
                self.assertEqual(self.run_sync(True), 0)
        self.magento.update.assert_not_called()

    def test_auth_failure_stops_remaining_updates(self):
        self.qbo.inventory.return_value = [item(), item("2", "BB-2")]
        self.magento.stock.side_effect = app.ApiError(401)
        with self.assertRaises(app.SyncError):
            self.run_sync(True)
        self.assertEqual(self.magento.stock.call_count, 1)

    def test_quickbooks_fetches_all_pages(self):
        http = Mock()
        http.request.side_effect = [
            {"QueryResponse": {"Item": [item(str(i)) for i in range(1000)]}},
            {"QueryResponse": {"Item": [item("1000")]}},
        ]
        store = Mock()
        store.get_valid_tokens.return_value = {"realm_id": "123", "access_token": "test-token"}
        qbo = app.QuickBooks({"base_url": "https://quickbooks.api.intuit.com"}, http, token_store=store)
        self.assertEqual(len(qbo.inventory()), 1001)
        self.assertIn("STARTPOSITION+1001", http.request.call_args.args[1])

    def test_failed_second_page_causes_no_partial_sync(self):
        http = Mock()
        http.request.side_effect = [
            {"QueryResponse": {"Item": [item(str(i)) for i in range(1000)]}},
            app.ApiError(503),
        ]
        store = Mock()
        store.get_valid_tokens.return_value = {"realm_id": "123", "access_token": "test-token"}
        qbo = app.QuickBooks({"base_url": "https://quickbooks.api.intuit.com"}, http, token_store=store)
        with self.assertRaises(app.ApiError):
            app.sync(qbo, self.magento, self.report, apply=True)
        self.magento.update.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_legacy_inventory_endpoint_preserves_settings_and_encodes_sku(self):
        http = Mock()
        with patch.dict(os.environ, {'MAGENTO_ACCESS_TOKEN': 'test-token'}):
            client = app.Magento({'rest_url': 'https://shop.example.invalid/rest/all/V1'}, http)
        http.request.side_effect = [{'type_id': 'simple'}, {'item_id': 4}]
        client.stock('SUIT /L')
        self.assertTrue(http.request.call_args.args[1].endswith('/stockItems/SUIT%20%2FL'))
        http.request.side_effect = None
        client.update('SUIT /L', 4, 0, False)
        self.assertEqual(http.request.call_args.args, (
            'PUT', 'https://shop.example.invalid/rest/all/V1/products/SUIT%20%2FL/stockItems/4',
            'test-token', {'stockItem': {'qty': 0, 'is_in_stock': False}}))

    def test_missing_magento_credentials_never_refreshes_quickbooks(self):
        with patch.dict(os.environ, {'MAGENTO_ACCESS_TOKEN': ''}), patch('stock_sync.QuickBooks') as qbo:
            self.assertEqual(app.run_stock_sync(), 1)
        qbo.assert_not_called()

    def test_report_is_private_and_default_run_never_applies(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('stock_sync.STOCK_CONFIG', {'report_dir': directory}), \
                    patch('stock_sync.Magento'), patch('stock_sync.QuickBooks'), \
                    patch('stock_sync.sync', return_value=0) as sync:
                self.assertEqual(app.run_stock_sync(), 0)
            sync.assert_called_once()
            self.assertFalse(sync.call_args.kwargs['apply'])
            report, = Path(directory).glob('*.csv')
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)

    def test_api_read_retries_rate_limit_without_logging_credentials(self):
        limited = Mock(status_code=429, headers={'Retry-After': '1'})
        success = Mock(status_code=200)
        success.json.return_value = {'ok': True}
        for response in [limited, success]:
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
        with patch('stock_sync.requests.request', side_effect=[limited, success]) as request, \
                patch('stock_sync.time.sleep') as sleep:
            self.assertEqual(app.Http().request('GET', 'https://example.invalid', 'test-token'), {'ok': True})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_uncertain_write_is_not_automatically_replayed(self):
        response = Mock(status_code=503)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('stock_sync.requests.request', return_value=response) as request:
            with self.assertRaises(app.ApiError):
                app.Http().request('PUT', 'https://example.invalid', 'test-token', {})
        request.assert_called_once()

    def test_cross_host_redirect_is_not_followed_with_bearer_token(self):
        response = Mock(status_code=302)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('stock_sync.requests.request', return_value=response):
            with self.assertRaises(app.ApiError):
                app.Http().request('GET', 'https://example.invalid', 'test-token')

    def test_stock_cli_never_starts_invoice_sync(self):
        import synclet
        with patch('stock_sync.run_stock_sync', return_value=0) as run, patch('synclet.Synclet') as invoices:
            self.assertEqual(synclet.main(['stock-sync', '--sku', 'BB-1']), 0)
        run.assert_called_once_with(apply=False, skus=['BB-1'])
        invoices.assert_not_called()
        with patch('stock_sync.run_stock_sync', return_value=1) as run:
            self.assertEqual(synclet.main(['stock-sync', '--apply']), 1)
        run.assert_called_once_with(apply=True, skus=None)


if __name__ == "__main__":
    unittest.main()
