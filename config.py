"""Configuration for the scheduled invoice sync; credentials live in .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / '.env')

MAGENTO_BASE_URL = os.getenv('MAGENTO_BASE_URL', 'https://www.bbwear.co.uk/onesaas_connect/index/index')
MAGENTO_ACCESS_KEY = os.getenv('MAGENTO_ACCESS_KEY', '')
PAGE_SIZE = int(os.getenv('PAGE_SIZE', 500))
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'database': os.getenv('DB_NAME', 'synclet_test'),
    'user': os.getenv('DB_USER', 'synclet_test'),
    'password': os.getenv('DB_PASSWORD', ''),
}

# Keep the existing default: cron imports orders and exports invoices in one run.
EXPORT_ON_SYNC = os.getenv('EXPORT_ON_SYNC', 'true').lower() == 'true'
QBO_DEBUG = os.getenv('QBO_DEBUG', 'true').lower() == 'true'
QBO_CONFIG = {
    'base_url': os.getenv('QBO_BASE_URL', 'https://quickbooks.api.intuit.com'),
    'vat_20_name': os.getenv('QBO_VAT_20_NAME', '20.0% S'),
    'vat_0_name': os.getenv('QBO_VAT_0_NAME', '0.0% Z'),
    'shipping_item_name': os.getenv('QBO_SHIPPING_ITEM_NAME', 'Default Shipping Product'),
    'adjustment_item_name': os.getenv('QBO_ADJUSTMENT_ITEM_NAME', 'Adjustment Item'),
    'customer_id': os.getenv('QBO_CUSTOMER_ID', '15990'),
}

# The invoice importer still uses the OneSaas connector. Stock uses Magento REST.
STOCK_CONFIG = {
    'rest_url': os.getenv('MAGENTO_REST_URL', 'https://www.bbwear.co.uk/rest/all/V1'),
    'token_env': 'MAGENTO_ACCESS_TOKEN',
    'report_dir': os.getenv('STOCK_REPORT_DIR', 'logs/stock-sync'),
}
