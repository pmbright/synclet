"""Magento API client for fetching orders."""

import requests
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from urllib.parse import urlencode
import time

logger = logging.getLogger(__name__)


class MagentoClient:
    """Client for interacting with Magento API."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Magento client with configuration."""
        self.base_url = config['base_url']
        self.access_key = config['access_key']
        self.action = config['action']
        self.session = requests.Session()
        self.page_size = config.get('page_size', 50)  # Default to 50 if not specified
        self.max_retries = config.get('max_retries', 3)
        self.retry_delay = config.get('retry_delay_seconds', 30)
        
    def fetch_orders(self, order_created_time: Optional[str] = None, 
                    last_updated_time: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch all orders from Magento API, handling pagination.
        
        Args:
            order_created_time: Filter orders created after this time (YYYY-MM-DDTHH:MM:SS)
            last_updated_time: Filter orders updated after this time (YYYY-MM-DDTHH:MM:SS)
            
        Returns:
            Dict containing the API response with all orders
        """
        all_orders = []
        page = 0
        total_fetched = 0
        api_version = None
        
        logger.info(f"Starting order fetch from Magento API")
        
        while True:
            params = {
                'AccessKey': self.access_key,
                'Action': self.action,
                'Page': page,
                'PageSize': self.page_size
            }
            
            if order_created_time:
                params['OrderCreatedTime'] = order_created_time
            elif last_updated_time:
                params['LastUpdatedTime'] = last_updated_time
                
            url = f"{self.base_url}?{urlencode(params)}"
            
            logger.info(f"Fetching page {page} (PageSize: {self.page_size})")
            logger.debug(f"URL: {url}")
            
            # Retry logic
            for attempt in range(self.max_retries):
                try:
                    response = self.session.get(url, timeout=30)
                    response.raise_for_status()
                    
                    data = response.json()
                    
                    # Store API version from first response
                    if api_version is None and 'OneSaas Version' in data:
                        api_version = data['OneSaas Version']
                    
                    orders = data.get('Orders', [])
                    orders_count = len(orders)
                    
                    logger.info(f"Page {page}: Fetched {orders_count} orders")
                    
                    if orders_count == 0:
                        # No more orders, we've reached the end
                        logger.info(f"No more orders found. Total pages: {page}")
                        break
                        
                    all_orders.extend(orders)
                    total_fetched += orders_count
                    
                    # If we got fewer orders than page size, we've reached the end
                    if orders_count < self.page_size:
                        logger.info(f"Received {orders_count} orders (less than page size {self.page_size}). This is the last page.")
                        break
                        
                    # Move to next page
                    page += 1
                    break  # Break out of retry loop
                    
                except requests.exceptions.RequestException as e:
                    if attempt < self.max_retries - 1:
                        logger.warning(f"Error fetching page {page} (attempt {attempt + 1}/{self.max_retries}): {e}")
                        logger.info(f"Retrying in {self.retry_delay} seconds...")
                        time.sleep(self.retry_delay)
                    else:
                        logger.error(f"Failed to fetch page {page} after {self.max_retries} attempts: {e}")
                        raise
                except ValueError as e:
                    logger.error(f"Error parsing JSON response for page {page}: {e}")
                    raise
            else:
                # If we've processed all pages successfully, break the main loop
                if len(orders) == 0:
                    break
                    
        logger.info(f"Completed fetching orders. Total orders: {total_fetched}, Pages: {page}")
        
        # Return combined response
        result = {}
        if api_version:
            result['OneSaas Version'] = api_version
        result['Orders'] = all_orders
        
        return result
            
    def parse_orders(self, response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse orders from API response.
        
        Args:
            response_data: Raw API response
            
        Returns:
            List of order dictionaries
        """
        orders = response_data.get('Orders', [])
        
        parsed_orders = []
        for order in orders:
            try:
                parsed_order = self._parse_single_order(order)
                parsed_orders.append(parsed_order)
            except Exception as e:
                logger.error(f"Error parsing order {order.get('Id', 'unknown')}: {e}")
                continue
                
        return parsed_orders
        
    def _parse_single_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a single order."""
        # Convert date strings to datetime objects for database storage
        order_date = self._parse_date(order.get('Date'))
        last_updated = self._parse_date(order.get('LastUpdatedDate'))
        
        parsed_order = {
            'Id': order['Id'],
            'OrderNumber': order['OrderNumber'],
            'Date': order_date,
            'LastUpdatedDate': last_updated,
            'Type': order.get('Type', 'Order'),
            'Status': order.get('Status'),
            'CurrencyCode': order.get('CurrencyCode'),
            'Total': order.get('Total'),
            'Contact': order.get('Contact', {}),
            'Addresses': order.get('Addresses', {}),
            'Items': order.get('Items', []),
            'Shipping': order.get('Shipping', {}),
            'Payments': order.get('Payments', {}),
            'Credits': order.get('Credits', []),
            'CustomFields': order.get('CustomFields', {})
        }
        
        return parsed_order
        
    def _parse_date(self, date_string: str) -> Optional[datetime]:
        """Parse date string to datetime object."""
        if not date_string:
            return None
            
        try:
            # Try different date formats
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S']:
                try:
                    return datetime.strptime(date_string, fmt)
                except ValueError:
                    continue
                    
            logger.warning(f"Could not parse date: {date_string}")
            return None
            
        except Exception as e:
            logger.error(f"Error parsing date {date_string}: {e}")
            return None
            
    def get_latest_order_date(self, orders: List[Dict[str, Any]]) -> Optional[datetime]:
        """Get the latest order date from a list of orders."""
        if not orders:
            return None
            
        dates = []
        for order in orders:
            if isinstance(order.get('LastUpdatedDate'), datetime):
                dates.append(order['LastUpdatedDate'])
            elif isinstance(order.get('Date'), datetime):
                dates.append(order['Date'])
                
        return max(dates) if dates else None
