import json
import os
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlencode
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from quickbooks import QuickBooks
from quickbooks.objects.item import Item

from config import (
    QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REDIRECT_URI,
    QBO_ENVIRONMENT, QBO_COMPANY_ID, QBO_TOKEN_FILE
)

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback"""
    def do_GET(self):
        # Parse the callback URL
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        if 'code' in query_params:
            self.server.auth_code = query_params['code'][0]
            self.server.realm_id = query_params.get('realmId', [None])[0]
            
            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'''
                <html>
                <body>
                <h1>Authorization Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                </body>
                </html>
            ''')
        else:
            # Send error response
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Authorization Failed!</h1></body></html>')
    
    def log_message(self, format, *args):
        # Suppress log messages
        pass

class QuickBooksAPI:
    def __init__(self):
        self.client_id = QBO_CLIENT_ID
        self.client_secret = QBO_CLIENT_SECRET
        self.redirect_uri = QBO_REDIRECT_URI
        self.environment = QBO_ENVIRONMENT
        self.company_id = QBO_COMPANY_ID
        self.token_file = QBO_TOKEN_FILE
        
        # Initialize auth client
        self.auth_client = AuthClient(
            client_id=self.client_id,
            client_secret=self.client_secret,
            environment=self.environment,
            redirect_uri=self.redirect_uri
        )
        
        self.access_token = None
        self.refresh_token = None
        self.realm_id = None
        
        # Try to load existing tokens
        self._load_tokens()
    
    def _load_tokens(self):
        """Load tokens from file if they exist"""
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token')
                    self.refresh_token = data.get('refresh_token')
                    self.realm_id = data.get('realm_id')
                    
                    # Check if tokens are expired
                    expires_at = data.get('expires_at')
                    if expires_at:
                        expires_at = datetime.fromisoformat(expires_at)
                        if expires_at <= datetime.now():
                            # Token expired, try to refresh
                            self._refresh_tokens()
                            
            except Exception as e:
                print(f"Error loading tokens: {e}")
    
    def _save_tokens(self):
        """Save tokens to file"""
        try:
            data = {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'realm_id': self.realm_id,
                'expires_at': (datetime.now() + timedelta(hours=1)).isoformat()
            }
            
            with open(self.token_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            print(f"Error saving tokens: {e}")
    
    def _refresh_tokens(self):
        """Refresh access token using refresh token"""
        if not self.refresh_token:
            print("No refresh token available")
            return False
            
        try:
            self.auth_client.refresh(refresh_token=self.refresh_token)
            self.access_token = self.auth_client.access_token
            self.refresh_token = self.auth_client.refresh_token
            self._save_tokens()
            return True
            
        except Exception as e:
            print(f"Error refreshing tokens: {e}")
            return False
    
    def authorize(self):
        """Perform OAuth2 authorization flow"""
        # Check if we have valid tokens
        if self.access_token and self.realm_id:
            print("Already authorized. Testing connection...")
            if self.test_connection():
                return True
            else:
                print("Existing authorization invalid, re-authorizing...")
        
        # Generate authorization URL
        scopes = [
            Scopes.ACCOUNTING
        ]
        
        auth_url = self.auth_client.get_authorization_url(scopes)
        
        print(f"\nOpening browser for QuickBooks authorization...")
        print(f"If browser doesn't open, visit this URL: {auth_url}")
        
        # Start local server to handle callback
        server = HTTPServer(('localhost', 8080), OAuthCallbackHandler)
        server.auth_code = None
        server.realm_id = None
        
        # Open browser in a separate thread
        webbrowser.open(auth_url)
        
        # Wait for callback
        print("\nWaiting for authorization callback...")
        server.timeout = 300  # 5 minute timeout
        
        while server.auth_code is None:
            server.handle_request()
            if server.auth_code:
                break
        
        if server.auth_code:
            # Exchange code for tokens
            try:
                self.auth_client.get_bearer_token(server.auth_code, realm_id=server.realm_id)
                self.access_token = self.auth_client.access_token
                self.refresh_token = self.auth_client.refresh_token
                self.realm_id = server.realm_id or self.company_id
                
                self._save_tokens()
                print("Authorization successful!")
                return True
                
            except Exception as e:
                print(f"Error getting tokens: {e}")
                return False
        else:
            print("Authorization timeout or cancelled")
            return False
    
    def get_client(self):
        """Get QuickBooks client instance"""
        if not self.access_token or not self.realm_id:
            if not self.authorize():
                return None
        
        return QuickBooks(
            auth_client=self.auth_client,
            refresh_token=self.refresh_token,
            company_id=self.realm_id or self.company_id
        )
    
    def test_connection(self):
        """Test the QuickBooks connection"""
        try:
            client = self.get_client()
            if not client:
                return False
            
            # Try to get company info
            company_info = client.company_info.get()
            print(f"Connected to QuickBooks company: {company_info.CompanyName}")
            print(f"Company ID: {company_info.Id}")
            return True
            
        except Exception as e:
            print(f"QuickBooks connection test failed: {e}")
            return False
    
    def get_inventory_items(self):
        """Get all inventory items from QuickBooks"""
        try:
            client = self.get_client()
            if not client:
                return None
            
            # Query for all active inventory items
            items = Item.filter(
                Active=True,
                Type='Inventory',
                qb=client
            )
            
            inventory_data = []
            for item in items:
                inventory_data.append({
                    'id': item.Id,
                    'name': item.Name,
                    'sku': item.Sku,
                    'qty_on_hand': float(item.QtyOnHand) if item.QtyOnHand else 0,
                    'active': item.Active
                })
            
            return inventory_data
            
        except Exception as e:
            print(f"Error fetching inventory items: {e}")
            return None
    
    def get_item_by_sku(self, sku):
        """Get a specific item by SKU"""
        try:
            client = self.get_client()
            if not client:
                return None
            
            # Query for item by SKU
            items = Item.filter(
                Sku=sku,
                qb=client
            )
            
            if items:
                item = items[0]
                return {
                    'id': item.Id,
                    'name': item.Name,
                    'sku': item.Sku,
                    'qty_on_hand': float(item.QtyOnHand) if item.QtyOnHand else 0,
                    'active': item.Active
                }
            
            return None
            
        except Exception as e:
            print(f"Error fetching item by SKU {sku}: {e}")
            return None
