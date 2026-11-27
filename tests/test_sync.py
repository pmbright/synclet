from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import synclet
from database import Database
from magento_api import MagentoAPI


class OfflineTests(unittest.TestCase):
    def setUp(self):
        for name in ['requests.sessions.Session.request', 'mysql.connector.connect']:
            guard = patch(name, side_effect=AssertionError('Unexpected live connection in offline test'))
            guard.start()
            self.addCleanup(guard.stop)
        self.output = io.StringIO()
        output = redirect_stdout(self.output)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)


class MagentoTests(OfflineTests):
    def setUp(self):
        super().setUp()
        self.api = MagentoAPI()
        self.api.page_size = 2
        self.api.access_key = 'SECRET-TEST-KEY'

    def test_pagination_keeps_date_filters_and_page_number(self):
        with patch('magento_api.requests.get') as get:
            get.return_value.json.side_effect = [{'Orders': [{'Id': '1'}, {'Id': '2'}]}, {'Orders': [{'Id': '3'}]}]
            result = self.api.fetch_all_orders('2026-01-01 00:00:00', '2026-11-27 10:00:00')
        self.assertEqual(len(result), 3)
        from urllib.parse import parse_qs, urlsplit
        params = parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual(params['Page'], ['1'])
        self.assertEqual(params['LastUpdatedTime'], ['2026-11-27T10:00:00'])
        self.assertEqual(params['OrderCreatedTime'], ['2026-01-01T00:00:00'])
        self.assertEqual(params['AccessKey'], ['SECRET-TEST-KEY'])
        self.assertNotIn('SECRET-TEST-KEY', self.output.getvalue())

    def test_connector_empty_string_is_end_of_pages(self):
        with patch('magento_api.requests.get') as get:
            get.return_value.json.side_effect = [{'Orders': [{'Id': '1'}, {'Id': '2'}]}, '']
            self.assertEqual(len(self.api.fetch_all_orders()), 2)

    def test_empty_first_page_is_successful_empty_import(self):
        with patch('magento_api.requests.get') as get:
            get.return_value.json.return_value = ''
            self.assertEqual(self.api.fetch_all_orders(), [])

    def test_failed_second_page_does_not_return_partial_orders(self):
        import requests
        response = Mock()
        response.json.return_value = {'Orders': [{'Id': '1'}, {'Id': '2'}]}
        with patch('magento_api.requests.get', side_effect=[response, requests.ConnectionError('SECRET-TEST-KEY')]):
            self.assertIsNone(self.api.fetch_all_orders())
        self.assertNotIn('SECRET-TEST-KEY', self.output.getvalue())

    def test_api_error_with_http_200_is_failure(self):
        for data in [{'Error': 'Invalid API key'}, {'unexpected': 'response'}, {'Orders': 'invalid'}]:
            with self.subTest(data=data), patch('magento_api.requests.get') as get:
                get.return_value.json.return_value = data
                self.assertIsNone(self.api.fetch_all_orders())


class RunnerTests(OfflineTests):
    def setUp(self):
        super().setUp()
        self.runner = synclet.Synclet()
        self.runner.db = Mock()
        self.runner.db.connect.return_value = True
        self.runner.db.create_tables.return_value = True
        self.runner.db.get_last_sync_time.return_value = None
        self.runner.api = Mock()
        self.runner.api.fetch_all_orders.return_value = []
        self.runner._record_sync_with_metadata = Mock()
        exporter = patch('qbo_exporter.QBOExporter')
        with patch.dict(os.environ, {'QBO_CLIENT_ID': 'test-client', 'QBO_CLIENT_SECRET': 'test-secret'}):
            self.exporter = exporter.start()
        self.addCleanup(exporter.stop)
        exporting = patch('synclet.EXPORT_ON_SYNC', True)
        exporting.start()
        self.addCleanup(exporting.stop)

    def test_initial_run_preserves_effective_initial_dates_and_exports_backlog(self):
        self.assertTrue(self.runner.sync_orders())
        self.runner.api.fetch_all_orders.assert_called_once_with(
            order_created_time='2026-01-01 00:00:00', last_updated_time='2026-05-20 08:52:31')
        self.exporter.return_value.export_orders.assert_called_once_with()
        self.runner.db.disconnect.assert_called_once()

    def test_incremental_run_keeps_two_hour_overlap(self):
        self.runner.db.get_last_sync_time.return_value = datetime(2026, 11, 27)
        self.runner._get_order_created_time = Mock(return_value='2026-06-01 00:00:00')
        class Clock(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 11, 27, 12, 0, 0)
        with patch('synclet.datetime', Clock):
            self.assertTrue(self.runner.sync_orders())
        self.runner.api.fetch_all_orders.assert_called_once_with(
            order_created_time='2026-06-01 00:00:00', last_updated_time='2026-11-27 10:00:00')

    def test_failed_fetch_stops_export_and_records_failure(self):
        self.runner.api.fetch_all_orders.return_value = None
        self.assertFalse(self.runner.sync_orders())
        self.exporter.assert_not_called()
        self.runner._record_sync_with_metadata.assert_not_called()
        self.assertFalse(self.runner.db.record_sync.call_args.kwargs['success'])
        self.runner.db.disconnect.assert_called_once()

    def test_failed_order_save_stops_export(self):
        self.runner.api.fetch_all_orders.return_value = [{'OrderNumber': '000123'}]
        self.runner.db.save_order.return_value = False
        self.assertFalse(self.runner.sync_orders())
        self.exporter.assert_not_called()

    def test_successful_import_records_latest_order_time_and_count(self):
        self.runner.api.fetch_all_orders.return_value = [
            {'OrderNumber': '1', 'LastUpdatedDate': '2026-11-27 10:00:00'},
            {'OrderNumber': '2', 'LastUpdatedDate': '2026-11-27 09:00:00'},
        ]
        self.assertTrue(self.runner.sync_orders())
        args = self.runner._record_sync_with_metadata.call_args.args
        self.assertEqual(args[1:3], (2, datetime(2026, 11, 27, 10)))

    def test_export_failure_makes_cron_run_fail(self):
        self.exporter.return_value.export_orders.side_effect = RuntimeError('QBO unavailable')
        self.assertFalse(self.runner.sync_orders())
        self.runner.db.disconnect.assert_called_once()

    def test_disabled_export_imports_only(self):
        with patch('synclet.EXPORT_ON_SYNC', False):
            self.assertTrue(self.runner.sync_orders())
        self.exporter.assert_not_called()

    def test_database_setup_failure_stops_before_magento(self):
        self.runner.db.create_tables.return_value = False
        self.assertFalse(self.runner.sync_orders())
        self.runner.api.fetch_all_orders.assert_not_called()

    def test_cron_command_and_exit_codes(self):
        for success, status in [(True, 0), (False, 1)]:
            with patch('synclet.Synclet', return_value=self.runner):
                with patch.object(self.runner, 'sync_orders', return_value=success):
                    self.assertEqual(synclet.main(['sync']), status)

    def test_sliding_window_calculation_preserved(self):
        self.runner.db.connection.cursor.return_value.fetchone.return_value = (
            json.dumps({'order_created_time': '2025-01-02 00:00:00'}),)
        class Clock(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 11, 27, 12)
        with patch('synclet.datetime', Clock):
            self.assertEqual(self.runner._get_order_created_time(), '2025-01-11 00:00:00')

    def test_metadata_column_absent_uses_legacy_history(self):
        # Exercise the real history writer rather than the runner's test stub.
        self.runner.db.connection.cursor.return_value.fetchone.return_value = (0,)
        synclet.Synclet._record_sync_with_metadata(self.runner, 'incremental', 2, None, {})
        self.runner.db.record_sync.assert_called_once_with('incremental', 2, None, True)


class DatabaseTests(OfflineTests):
    def test_bootstrap_keeps_export_ledgers_without_stock_or_destructive_sql(self):
        db = Database()
        db.connection = Mock()
        self.assertTrue(db.create_tables())
        statements = '\n'.join(call.args[0] for call in db.connection.cursor.return_value.execute.call_args_list)
        self.assertIn('CREATE TABLE IF NOT EXISTS csv_export_history', statements)
        self.assertIn('CREATE TABLE IF NOT EXISTS qbo_export_history', statements)
        for excluded in ['stock_sync_history', 'product_mapping', 'DROP TABLE', 'TRUNCATE', 'ALTER TABLE']:
            self.assertNotIn(excluded, statements)


if __name__ == '__main__':
    unittest.main()
