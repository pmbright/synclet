"""Tests use temporary synthetic tokens only; never load the supplied live .env."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import stock_sync


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tokens.json'
        environment = {'QBO_CLIENT_ID': 'test-id', 'QBO_CLIENT_SECRET': 'test-secret',
                       'QBO_TOKEN_FILE': str(self.path)}
        dotenv = types.ModuleType('dotenv')
        dotenv.load_dotenv = Mock()
        source = Path(__file__).resolve().parent.parent / 'qbo_token_store.py'
        with patch.dict(os.environ, environment), patch.dict('sys.modules', {'dotenv': dotenv}):
            spec = importlib.util.spec_from_file_location('test_token_store', source)
            self.store = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.store)
        self.old = {'access_token': 'old-access', 'refresh_token': 'old-refresh',
                    'realmId': '123', 'expires_at': 1, 'custom_metadata': 'keep'}
        self.path.write_text(json.dumps(self.old))
        self.response = Mock()
        self.response.json.return_value = {'access_token': 'new-access',
                                           'refresh_token': 'new-refresh', 'expires_in': 3600}

    def test_rotated_tokens_saved_atomically_with_realm_and_metadata(self):
        with patch.object(self.store.requests, 'post', return_value=self.response) as post:
            result = self.store.get_valid_tokens()
        self.assertEqual(result['realm_id'], '123')
        self.assertEqual(result['custom_metadata'], 'keep')
        self.assertEqual(json.loads(self.path.read_text())['refresh_token'], 'new-refresh')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(post.call_args.kwargs['data']['refresh_token'], 'old-refresh')

    def test_concurrent_callers_refresh_once_and_share_latest_tokens(self):
        with patch.object(self.store.requests, 'post', return_value=self.response) as post:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.store.get_valid_tokens(), range(8)))
        self.assertEqual(post.call_count, 1)
        self.assertTrue(all(result['access_token'] == 'new-access' for result in results))

    def test_failed_refresh_preserves_previous_file(self):
        self.response.raise_for_status.side_effect = RuntimeError('test failure')
        before = self.path.read_bytes()
        with patch.object(self.store.requests, 'post', return_value=self.response):
            with self.assertRaises(RuntimeError):
                self.store.get_valid_tokens()
        self.assertEqual(self.path.read_bytes(), before)

    def test_iso_expiry_from_older_application_is_supported(self):
        self.old['expires_at'] = '2000-01-01T00:00:00'
        self.path.write_text(json.dumps(self.old))
        with patch.object(self.store.requests, 'post', return_value=self.response) as post:
            self.assertEqual(self.store.get_access_token(), 'new-access')
        post.assert_called_once()

    def test_rejected_access_token_refreshes_even_before_expiry(self):
        self.old['expires_at'] = time.time() + 3600
        self.path.write_text(json.dumps(self.old))
        with patch.object(self.store.requests, 'post', return_value=self.response) as post:
            self.store.get_valid_tokens(rejected_access_token='old-access')
        post.assert_called_once()

    def test_rejection_of_previous_token_does_not_refresh_newer_token(self):
        with patch.object(self.store.requests, 'post', return_value=self.response) as post:
            self.store.get_valid_tokens()
            result = self.store.get_valid_tokens(rejected_access_token='old-access')
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result['access_token'], 'new-access')

    def test_stock_job_reuses_store_and_company_and_handles_401(self):
        store = Mock()
        store.get_valid_tokens.side_effect = [
            {'access_token': 'old-access', 'realm_id': '123'},
            {'access_token': 'new-access', 'realm_id': '123'},
        ]
        http = Mock()
        http.request.side_effect = [stock_sync.ApiError(401), {'QueryResponse': {}}]
        qbo = stock_sync.QuickBooks({'base_url': 'https://quickbooks.api.intuit.com'}, http, token_store=store)
        self.assertEqual(qbo.inventory(), [])
        store.get_valid_tokens.assert_called_with(rejected_access_token='old-access')
        self.assertIn('/company/123/', http.request.call_args.args[1])
        self.assertEqual(http.request.call_args.args[2], 'new-access')


if __name__ == '__main__':
    unittest.main()
