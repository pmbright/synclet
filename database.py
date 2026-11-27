import mysql.connector
from mysql.connector import Error
import json
from datetime import datetime
from config import DB_CONFIG

def safe_float(value, default=0.0):
    """Convert API numeric values safely; Magento may return null for numeric fields."""
    if value is None or value == "":
        return default
    return float(value)


class Database:
    def __init__(self):
        self.connection = None
        
    def connect(self):
        """Establish database connection"""
        try:
            self.connection = mysql.connector.connect(**DB_CONFIG)
            if self.connection.is_connected():
                print(f"Connected to MySQL database: {DB_CONFIG['database']}")
                return True
        except Error as e:
            print(f"Error connecting to MySQL: {e}")
            return False
    
    def disconnect(self):
        """Close database connection"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            print("Database connection closed")
    
    def create_tables(self):
        """Create necessary tables if they don't exist"""
        create_sync_history = """
        CREATE TABLE IF NOT EXISTS sync_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sync_time DATETIME NOT NULL,
            sync_type ENUM('initial', 'incremental') NOT NULL,
            orders_synced INT DEFAULT 0,
            last_order_time DATETIME,
            success BOOLEAN DEFAULT TRUE,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        
        create_orders_table = """
        CREATE TABLE IF NOT EXISTS orders (
            id INT PRIMARY KEY,
            order_number VARCHAR(50) NOT NULL,
            replace_order_number VARCHAR(50),
            order_date DATETIME NOT NULL,
            last_updated_date DATETIME NOT NULL,
            order_type VARCHAR(50),
            status VARCHAR(50),
            currency_code VARCHAR(10),
            notes TEXT,
            tags TEXT,
            discounts DECIMAL(10, 4),
            total DECIMAL(10, 4),
            shipping_method VARCHAR(255),
            shipping_amount DECIMAL(10, 4),
            shipping_tax_amount DECIMAL(10, 4),
            payment_method VARCHAR(100),
            payment_amount DECIMAL(10, 4),
            raw_json JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_order_number (order_number),
            INDEX idx_order_date (order_date),
            INDEX idx_last_updated (last_updated_date)
        )
        """
        
        create_order_items_table = """
        CREATE TABLE IF NOT EXISTS order_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            product_id VARCHAR(50),
            product_code VARCHAR(100),
            product_name VARCHAR(255),
            quantity DECIMAL(10, 4),
            price DECIMAL(10, 4),
            unit_price_ex_tax DECIMAL(10, 4),
            tax_rate DECIMAL(5, 4),
            tax_amount DECIMAL(10, 4),
            line_total_inc_tax DECIMAL(10, 4),
            discount DECIMAL(10, 4) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            INDEX idx_order_id (order_id)
        )
        """
        
        create_order_addresses_table = """
        CREATE TABLE IF NOT EXISTS order_addresses (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            address_type ENUM('billing', 'shipping') NOT NULL,
            salutation VARCHAR(50),
            first_name VARCHAR(100),
            last_name VARCHAR(100),
            organization_name VARCHAR(255),
            work_phone VARCHAR(50),
            line1 VARCHAR(255),
            line2 VARCHAR(255),
            city VARCHAR(100),
            post_code VARCHAR(20),
            state VARCHAR(100),
            country_code VARCHAR(10),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            INDEX idx_order_id (order_id)
        )
        """
        
        create_order_credits_table = """
        CREATE TABLE IF NOT EXISTS order_credits (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            entity_id VARCHAR(50),
            store_id VARCHAR(50),
            adjustment_positive DECIMAL(10, 4),
            adjustment_negative DECIMAL(10, 4),
            grand_total DECIMAL(10, 4),
            increment_id VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            INDEX idx_order_id (order_id)
        )
        """
        
        try:
            cursor = self.connection.cursor()
            
            # Create all tables
            cursor.execute(create_sync_history)
            cursor.execute(create_orders_table)
            cursor.execute(create_order_items_table)
            cursor.execute(create_order_addresses_table)
            cursor.execute(create_order_credits_table)
            
            self.connection.commit()
            print("Database tables created successfully")
            
        except Error as e:
            print(f"Error creating tables: {e}")
            self.connection.rollback()
            
    def _add_missing_columns(self):
        """Add any missing columns to existing tables"""
        try:
            cursor = self.connection.cursor()
            
            # Check if discount column exists in order_items table
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'order_items' 
                AND COLUMN_NAME = 'discount'
            """)
            
            if cursor.fetchone()[0] == 0:
                # Add discount column if it doesn't exist
                cursor.execute("ALTER TABLE order_items ADD COLUMN discount DECIMAL(10, 4) DEFAULT 0")
                self.connection.commit()
                print("Added discount column to order_items table")
                
        except Error as e:
            # Ignore errors if table doesn't exist yet
            pass
            
    def get_last_sync_time(self):
        """Get the last successful sync time"""
        query = """
        SELECT sync_time, last_order_time 
        FROM sync_history 
        WHERE success = TRUE 
        ORDER BY sync_time DESC 
        LIMIT 1
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query)
            result = cursor.fetchone()
            
            if result:
                # Return the last order time if available, otherwise the sync time
                return result[1] if result[1] else result[0]
            
            return None
            
        except Error as e:
            print(f"Error getting last sync time: {e}")
            return None
    
    def save_order(self, order_data):
        """Save a single order and its related data"""
        try:
            cursor = self.connection.cursor()
            
            # Fix null shipping method in the raw JSON before processing
            if 'Shipping' in order_data and order_data['Shipping'] is not None:
                if order_data['Shipping'].get('ShippingMethod') is None:
                    # Update the shipping method in the order data
                    order_data['Shipping']['ShippingMethod'] = "Shipping and Collection - FREE UK SHIPPING for orders of £250 and over (Royal Mail Tracked 48)"
            
            # Insert or update main order
            order_query = """
            INSERT INTO orders (
                id, order_number, replace_order_number, order_date, last_updated_date,
                order_type, status, currency_code, notes, tags, discounts, total,
                shipping_method, shipping_amount, shipping_tax_amount,
                payment_method, payment_amount, raw_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON DUPLICATE KEY UPDATE
                last_updated_date = VALUES(last_updated_date),
                status = VALUES(status),
                total = VALUES(total),
                raw_json = VALUES(raw_json),
                updated_at = CURRENT_TIMESTAMP
            """
            
            # Extract shipping info
            shipping = order_data.get('Shipping', {})
            shipping_method = shipping.get('ShippingMethod', '')
            shipping_amount = safe_float(shipping.get('Amount', 0))
            shipping_tax = safe_float(shipping.get('Taxes', {}).get('TaxAmount', 0))
            
            # Extract payment info
            payment = order_data.get('Payments', {}).get('PaymentMethod', {})
            payment_method = payment.get('MethodName', '')
            payment_amount = safe_float(payment.get('Amount', 0))
            
            cursor.execute(order_query, (
                int(order_data['Id']),
                order_data['OrderNumber'],
                order_data.get('ReplaceOrderNumber', ''),
                order_data['Date'],
                order_data['LastUpdatedDate'],
                order_data.get('Type', 'Order'),
                order_data.get('Status', ''),
                order_data.get('CurrencyCode', 'GBP'),
                order_data.get('Notes', ''),
                order_data.get('Tags', ''),
                safe_float(order_data.get('Discounts', 0)),
                safe_float(order_data.get('Total', 0)),
                shipping_method,
                shipping_amount,
                shipping_tax,
                payment_method,
                payment_amount,
                json.dumps(order_data)
            ))
            
            order_id = int(order_data['Id'])
            
            # Delete existing related records for update
            cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
            cursor.execute("DELETE FROM order_addresses WHERE order_id = %s", (order_id,))
            cursor.execute("DELETE FROM order_credits WHERE order_id = %s", (order_id,))
            
            # Insert order items
            for item in order_data.get('Items', []):
                item_query = """
                INSERT INTO order_items (
                    order_id, product_id, product_code, product_name, quantity,
                    price, unit_price_ex_tax, tax_rate, tax_amount, line_total_inc_tax, discount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                taxes = item.get('Taxes', {})
                tax_rate = safe_float(taxes.get('TaxRate', 0)) if taxes else 0
                tax_amount = safe_float(taxes.get('TaxAmount', 0)) if taxes else 0
                
                cursor.execute(item_query, (
                    order_id,
                    item.get('ProductId', ''),
                    item.get('ProductCode', ''),
                    item.get('ProductName', ''),
                    safe_float(item.get('Quantity', 0)),
                    safe_float(item.get('Price', 0)),
                    safe_float(item.get('UnitPriceExTax', 0)),
                    tax_rate,
                    tax_amount,
                    safe_float(item.get('LineTotalIncTax', 0)),
                    safe_float(item.get('Discount', 0))
                ))
            
            # Insert addresses
            addresses = order_data.get('Addresses', {})
            
            # Billing address
            if 'BillingAddress' in addresses:
                addr = addresses['BillingAddress']
                self._save_address(cursor, order_id, 'billing', addr)
            
            # Shipping address
            if 'ShippingAddress' in addresses:
                addr = addresses['ShippingAddress']
                self._save_address(cursor, order_id, 'shipping', addr)
            
            # Insert credits if any
            for credit in order_data.get('Credits', []):
                credit_query = """
                INSERT INTO order_credits (
                    order_id, entity_id, store_id, adjustment_positive,
                    adjustment_negative, grand_total, increment_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                
                cursor.execute(credit_query, (
                    order_id,
                    credit.get('entity_id', ''),
                    credit.get('store_id', ''),
                    safe_float(credit.get('adjustment_positive', 0)),
                    safe_float(credit.get('adjustment_negative', 0)),
                    safe_float(credit.get('grand_total', 0)),
                    credit.get('increment_id', '')
                ))
            
            self.connection.commit()
            return True
            
        except Error as e:
            print(f"Error saving order {order_data.get('OrderNumber', 'Unknown')}: {e}")
            self.connection.rollback()
            return False
        except Exception as e:
            print(f"Error saving order {order_data.get('OrderNumber', 'Unknown')}: {e}")
            print("[BAD ORDER JSON]")
            print(json.dumps(order_data, indent=2))
            self.connection.rollback()
            return False
    
    def _save_address(self, cursor, order_id, address_type, address_data):
        """Helper method to save address data"""
        address_query = """
        INSERT INTO order_addresses (
            order_id, address_type, salutation, first_name, last_name,
            organization_name, work_phone, line1, line2, city,
            post_code, state, country_code
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        cursor.execute(address_query, (
            order_id,
            address_type,
            address_data.get('Salutation', ''),
            address_data.get('FirstName', ''),
            address_data.get('LastName', ''),
            address_data.get('OrganizationName', ''),
            address_data.get('WorkPhone', ''),
            address_data.get('Line1', ''),
            address_data.get('Line2', ''),
            address_data.get('City', ''),
            address_data.get('PostCode', ''),
            address_data.get('State', ''),
            address_data.get('CountryCode', '')
        ))
    
    def record_sync(self, sync_type, orders_synced, last_order_time=None, success=True, error_message=None):
        """Record sync history"""
        query = """
        INSERT INTO sync_history (
            sync_time, sync_type, orders_synced, last_order_time, success, error_message
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, (
                datetime.now(),
                sync_type,
                orders_synced,
                last_order_time,
                success,
                error_message
            ))
            self.connection.commit()
            
        except Error as e:
            print(f"Error recording sync history: {e}")
    
    def clear_all_data(self):
        """Drop all tables from the database"""
        try:
            cursor = self.connection.cursor()
            
            # Disable foreign key checks temporarily
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            
            # Drop all tables
            cursor.execute("DROP TABLE IF EXISTS csv_export_history")
            cursor.execute("DROP TABLE IF EXISTS order_credits")
            cursor.execute("DROP TABLE IF EXISTS order_addresses")
            cursor.execute("DROP TABLE IF EXISTS order_items")
            cursor.execute("DROP TABLE IF EXISTS orders")
            cursor.execute("DROP TABLE IF EXISTS sync_history")
            cursor.execute("DROP TABLE IF EXISTS stock_sync_history")
            cursor.execute("DROP TABLE IF EXISTS product_mapping")
            
            # Re-enable foreign key checks
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            
            self.connection.commit()
            print("All tables dropped successfully")
            
        except Error as e:
            print(f"Error dropping tables: {e}")
            self.connection.rollback()
    
    def create_stock_sync_tables(self):
        """Create tables for stock synchronization tracking"""
        create_stock_sync_history = """
        CREATE TABLE IF NOT EXISTS stock_sync_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sync_time DATETIME NOT NULL,
            products_synced INT DEFAULT 0,
            success BOOLEAN DEFAULT TRUE,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        
        create_product_mapping = """
        CREATE TABLE IF NOT EXISTS product_mapping (
            id INT AUTO_INCREMENT PRIMARY KEY,
            qbo_item_id VARCHAR(50),
            qbo_sku VARCHAR(100),
            magento_product_code VARCHAR(100) UNIQUE,
            last_sync_qty DECIMAL(10, 2),
            last_sync_time DATETIME,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_qbo_sku (qbo_sku),
            INDEX idx_magento_code (magento_product_code)
        )
        """
        
        create_csv_export_history = """
        CREATE TABLE IF NOT EXISTS csv_export_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            order_number VARCHAR(50) NOT NULL,
            export_time DATETIME NOT NULL,
            file_path VARCHAR(500),
            export_type VARCHAR(20) DEFAULT 'full',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            UNIQUE KEY unique_order_export (order_id),
            INDEX idx_order_number (order_number),
            INDEX idx_export_time (export_time)
        )
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(create_stock_sync_history)
            cursor.execute(create_product_mapping)
            cursor.execute(create_csv_export_history)
            self.connection.commit()
            print("Stock sync and CSV export tables created successfully")
            
        except Error as e:
            print(f"Error creating stock sync tables: {e}")
            self.connection.rollback()
    
    def record_stock_sync(self, products_synced, success=True, error_message=None):
        """Record stock sync history"""
        query = """
        INSERT INTO stock_sync_history (sync_time, products_synced, success, error_message)
        VALUES (%s, %s, %s, %s)
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, (
                datetime.now(),
                products_synced,
                success,
                error_message
            ))
            self.connection.commit()
            
        except Error as e:
            print(f"Error recording stock sync history: {e}")
    
    def update_product_mapping(self, qbo_item_id, qbo_sku, magento_code, qty):
        """Update or insert product mapping"""
        query = """
        INSERT INTO product_mapping (qbo_item_id, qbo_sku, magento_product_code, last_sync_qty, last_sync_time)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            qbo_item_id = VALUES(qbo_item_id),
            qbo_sku = VALUES(qbo_sku),
            last_sync_qty = VALUES(last_sync_qty),
            last_sync_time = VALUES(last_sync_time)
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, (
                qbo_item_id,
                qbo_sku,
                magento_code,
                qty,
                datetime.now()
            ))
            self.connection.commit()
            
        except Error as e:
            print(f"Error updating product mapping: {e}")
    
    def record_csv_export(self, order_id, order_number, file_path, export_type='full'):
        """Record that an order has been exported to CSV"""
        query = """
        INSERT INTO csv_export_history (order_id, order_number, export_time, file_path, export_type)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            export_time = VALUES(export_time),
            file_path = VALUES(file_path),
            export_type = VALUES(export_type)
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, (
                order_id,
                order_number,
                datetime.now(),
                file_path,
                export_type
            ))
            self.connection.commit()
            return True
            
        except Error as e:
            print(f"Error recording CSV export: {e}")
            self.connection.rollback()
            return False
    
    def get_exported_order_ids(self):
        """Get list of order IDs that have already been exported to CSV"""
        query = """
        SELECT DISTINCT order_id 
        FROM csv_export_history
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query)
            return [row[0] for row in cursor.fetchall()]
            
        except Error as e:
            print(f"Error getting exported orders: {e}")
            return []
    
    def is_order_exported(self, order_id):
        """Check if a specific order has been exported"""
        query = """
        SELECT COUNT(*) 
        FROM csv_export_history 
        WHERE order_id = %s
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, (order_id,))
            result = cursor.fetchone()
            return result[0] > 0
            
        except Error as e:
            print(f"Error checking order export status: {e}")
            return False
    
    def get_complete_orders_for_export(self):
        """Get complete orders that haven't been exported yet"""
        query = """
        SELECT o.id, o.order_number, o.order_date, o.last_updated_date,
               o.order_type, o.status, o.currency_code, o.notes, o.tags,
               o.discounts, o.total, o.shipping_method, o.shipping_amount,
               o.shipping_tax_amount, o.payment_method, o.payment_amount,
               o.raw_json
        FROM orders o
        LEFT JOIN csv_export_history ceh ON o.id = ceh.order_id
        WHERE o.status = 'complete' 
        AND ceh.order_id IS NULL
        ORDER BY o.order_date
        """
        
        try:
            cursor = self.connection.cursor(dictionary=True)
            cursor.execute(query)
            return cursor.fetchall()
            
        except Error as e:
            print(f"Error getting orders for export: {e}")
            return []
    
    def clear_csv_export_history(self):
        """Clear CSV export history (for testing)"""
        query = "TRUNCATE TABLE csv_export_history"
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(query)
            self.connection.commit()
            print("CSV export history cleared")
            
        except Error as e:
            print(f"Error clearing CSV export history: {e}")
            self.connection.rollback()
    
    def drop_all_tables(self):
        """Drop all tables - WARNING: This will delete everything!"""
        try:
            cursor = self.connection.cursor()
            
            # Disable foreign key checks
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            
            # Drop all tables
            cursor.execute("DROP TABLE IF EXISTS csv_export_history")
            cursor.execute("DROP TABLE IF EXISTS order_credits")
            cursor.execute("DROP TABLE IF EXISTS order_addresses")
            cursor.execute("DROP TABLE IF EXISTS order_items")
            cursor.execute("DROP TABLE IF EXISTS orders")
            cursor.execute("DROP TABLE IF EXISTS sync_history")
            cursor.execute("DROP TABLE IF EXISTS stock_sync_history")
            cursor.execute("DROP TABLE IF EXISTS product_mapping")
            
            # Re-enable foreign key checks
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            
            self.connection.commit()
            print("All tables dropped successfully")
            
        except Error as e:
            print(f"Error dropping tables: {e}")
            self.connection.rollback()

