"""Read Magento orders through the installed OneSaas connector."""
from datetime import datetime
from urllib.parse import urlencode

import requests

from config import MAGENTO_BASE_URL, MAGENTO_ACCESS_KEY, PAGE_SIZE


class MagentoAPI:
    def __init__(self):
        self.base_url = MAGENTO_BASE_URL
        self.access_key = MAGENTO_ACCESS_KEY
        self.page_size = PAGE_SIZE
        if self.page_size < 1:
            raise ValueError('PAGE_SIZE must be positive')

    def _build_url(self, params):
        return f"{self.base_url}?{urlencode({'AccessKey': self.access_key, 'Action': 'Orders', 'PageSize': self.page_size, **params})}"

    def fetch_orders(self, order_created_time=None, last_updated_time=None, page=0):
        params = {}
        for name, value in [('OrderCreatedTime', order_created_time), ('LastUpdatedTime', last_updated_time)]:
            if value:
                params[name] = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%S')
        if page > 0:
            params['Page'] = page
        try:
            # Do not log the URL: it contains the connector access key.
            print(f"Fetching Magento order page {page}")
            response = requests.get(self._build_url(params), timeout=30)
            response.raise_for_status()
            data = response.json()
            # The installed connector returns JSON "" when there are no more pages,
            # including an empty first page. This is different from a request failure.
            if data == "":
                return {'Orders': []}
            if not isinstance(data, dict) or data.get('Error') or data.get('ErrorCode'):
                raise ValueError('Unexpected Magento API response')
            if 'Orders' not in data and 'OneSaas Version' not in data:
                raise ValueError('Magento response has neither orders nor connector version')
            if not isinstance(data.get('Orders', []), list):
                raise ValueError('Magento Orders must be a list')
            return data
        except (requests.exceptions.RequestException, ValueError) as error:
            # Requests exceptions can contain the URL and key; log only their type.
            print(f"Error fetching Magento orders ({type(error).__name__})")
            return None

    def fetch_all_orders(self, order_created_time=None, last_updated_time=None):
        orders, page = [], 0
        while True:
            response = self.fetch_orders(order_created_time, last_updated_time, page)
            if response is None:
                return None
            batch = response.get('Orders', [])
            orders.extend(batch)
            print(f"Fetched {len(batch)} orders from page {page}")
            if len(batch) < self.page_size:
                return orders
            page += 1
