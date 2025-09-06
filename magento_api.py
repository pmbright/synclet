import requests
import json
from datetime import datetime
from urllib.parse import urlencode
from config import MAGENTO_BASE_URL, MAGENTO_ACCESS_KEY, PAGE_SIZE

class MagentoAPI:
    def __init__(self):
        self.base_url = MAGENTO_BASE_URL
        self.access_key = MAGENTO_ACCESS_KEY
        self.page_size = PAGE_SIZE
        
    def _build_url(self, params):
        """Build the full URL with parameters"""
        # Always include AccessKey and Action
        params['AccessKey'] = self.access_key
        params['Action'] = 'Orders'
        
        # Set page size if not specified
        if 'PageSize' not in params:
            params['PageSize'] = self.page_size
            
        return f"{self.base_url}?{urlencode(params)}"
    
    def fetch_orders(self, order_created_time=None, last_updated_time=None, page=0):
        """
        Fetch orders from Magento API
        
        Args:
            order_created_time: DateTime string for initial sync (format: YYYY-MM-DD HH:MM:SS)
            last_updated_time: DateTime string for incremental sync (format: YYYY-MM-DD HH:MM:SS)
            page: Page number (0-based)
            
        Returns:
            dict: JSON response from the API
        """
        params = {}
        
        # Convert datetime strings to ISO format expected by the API
        if order_created_time:
            # Convert to ISO format with timezone
            dt = datetime.strptime(order_created_time, '%Y-%m-%d %H:%M:%S')
            params['OrderCreatedTime'] = dt.strftime('%Y-%m-%dT%H:%M:%S')
            
        if last_updated_time:
            # Convert to ISO format with timezone
            dt = datetime.strptime(last_updated_time, '%Y-%m-%d %H:%M:%S')
            params['LastUpdatedTime'] = dt.strftime('%Y-%m-%dT%H:%M:%S')
            
        # Add page parameter if not first page
        if page > 0:
            params['Page'] = page
            
        url = self._build_url(params)
        
        try:
            print(f"Fetching orders from: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching orders from Magento: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {e}")
            return None
    
    def fetch_all_orders(self, order_created_time=None, last_updated_time=None):
        """
        Fetch all orders, handling pagination
        
        Args:
            order_created_time: DateTime string for initial sync
            last_updated_time: DateTime string for incremental sync
            
        Returns:
            list: All orders from all pages
        """
        all_orders = []
        page = 0
        
        while True:
            response = self.fetch_orders(
                order_created_time=order_created_time,
                last_updated_time=last_updated_time,
                page=page
            )
            
            if not response:
                break
                
            orders = response.get('Orders', [])
            
            if not orders:
                break
                
            all_orders.extend(orders)
            print(f"Fetched {len(orders)} orders from page {page}")
            
            # If we got fewer orders than page size, we're done
            if len(orders) < self.page_size:
                break
                
            page += 1
            
        return all_orders
    
    def test_connection(self):
        """Test the API connection by fetching a small number of recent orders"""
        params = {
            'PageSize': 1,
            'OrderCreatedTime': '2025-01-01T00:00:00'
        }
        
        url = self._build_url(params)
        
        try:
            print(f"Testing connection to: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'OneSaas Version' in data:
                print(f"Connection successful! OneSaas Version: {data['OneSaas Version']}")
                return True
            else:
                print("Unexpected response format")
                return False
                
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
