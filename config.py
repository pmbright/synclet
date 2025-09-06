import os
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

# Magento API Configuration
MAGENTO_BASE_URL = os.getenv('MAGENTO_BASE_URL', 'https://www.bbwear.co.uk/onesaas_connect/index/index')
MAGENTO_ACCESS_KEY = os.getenv('MAGENTO_ACCESS_KEY', 'aa26e14fc1a499c3757557a84f805e7d10b82339f3b00c1982b8350592a97de5')

# Database Configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'database': os.getenv('DB_NAME', 'synclet_test'),
    'user': os.getenv('DB_USER', 'synclet_test'),
    'password': os.getenv('DB_PASSWORD', 'Bingo-Shrubbery-Crushing-428')
}

# Initial sync date
INITIAL_SYNC_DATE = os.getenv('INITIAL_SYNC_DATE', '2025-07-01 07:41:16')

# Page size for API requests
PAGE_SIZE = 50
