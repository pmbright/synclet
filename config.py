import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Magento API Configuration
MAGENTO_BASE_URL = os.getenv(
    'MAGENTO_BASE_URL',
    'https://www.bbwear.co.uk/onesaas_connect/index/index'
)
MAGENTO_ACCESS_KEY = os.getenv(
    'MAGENTO_ACCESS_KEY',
    ''
)

# Database Configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'database': os.getenv('DB_NAME', 'synclet_test'),
    'user': os.getenv('DB_USER', 'synclet_test'),
    'password': os.getenv('DB_PASSWORD', '')
}

# Page size for API requests
PAGE_SIZE = int(os.getenv('PAGE_SIZE', 500))

# CSV export configuration
EXPORT_ON_SYNC = os.getenv('EXPORT_ON_SYNC', 'true').lower() == 'true'
CSV_EXPORT_DIR = os.getenv('CSV_EXPORT_DIR', 'exports')
CSV_EXPORT_SIMPLE = os.getenv('CSV_EXPORT_SIMPLE', 'true').lower() == 'true'
CSV_EXPORT_COMPLETE_ONLY = os.getenv('CSV_EXPORT_COMPLETE_ONLY', 'false').lower() == 'true'
CSV_EXPORT_TRACK = os.getenv('CSV_EXPORT_TRACK', 'true').lower() == 'true'



QBO_CONFIG = {
    "base_url": "https://quickbooks.api.intuit.com",
    #"base_url": "https://sandbox-quickbooks.api.intuit.com",
    #"realm_id": "123146494252114",
    #"realm_id": "193514804510844",
    
    "vat_20_name": "20.0% S",
    "vat_0_name": "0.0% Z",

    "shipping_item_name": "Default Shipping Product",
    "adjustment_item_name": "Adjustment Item",

    ##"customer_id": "1970"
    "customer_id": "15990"
}

QBO_DEBUG = True
QBO_DRY_RUN = True
