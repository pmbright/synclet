"""Database module for Synclet - handles MySQL operations."""

import mysql.connector
from mysql.connector import Error
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and operations for Synclet."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize database manager with configuration."""
        self.config = config
        self.connection = None
        
    def connect(self) -> bool:
        """Establish connection to MySQL database."""
        try:
            self.connection = mysql.connector.connect(
                host=self.config['host'],
                port=self.config['port'],
                database=self.config['database'],
                user=self.config['user'],
                password=self.config['password']
            )
            if self.connection.is_connected():
                logger.info(f"Connected to MySQL database: {self.config['database']}")
                return True
        except Error as e:
            logger.error(f"Error connecting to MySQL: {e}")
            return False
            
    def disconnect(self):
        """Close database connection."""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            logger.info("Database connection closed")
            
    def create_tables(self):
        """Create necessary database tables if they don't exist."""
        cursor = self.connection.cursor()
        
        # Sync history table
        sync_history_sql = """
        CREATE TABLE IF NOT EXISTS sync_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sync_type VARCHAR(50) NOT NULL,
            sync_start DATETIME NOT NULL,
            sync_end DATETIME,
            last_order_date DATETIME,
            orders_processed INT DEFAULT 0,
            credits_processed INT DEFAULT 0,
            status VARCHAR(20) NOT NULL,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_sync_type (sync_type),
            INDEX idx_status (status),
            INDEX idx_sync_start (sync_start)
        )
        """
        
        # Processed orders table
        processed_orders_sql = """
        CREATE TABLE IF NOT EXISTS processed_orders (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id VARCHAR(50) NOT NULL UNIQUE,
            order_number VARCHAR(50) NOT NULL,
            order_date DATETIME NOT NULL,
            last_updated DATETIME NOT NULL,
            total_amount DECIMAL(10, 4),
            currency_code VARCHAR(3),
            status VARCHAR(50),
            sync_history_id INT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sync_history_id) REFERENCES sync_history(id),
            INDEX idx_order_id (order_id),
            INDEX idx_order_number (order_number),
            INDEX idx_order_date (order_date)
        )
        """
        
        # Credit memos table
        credit_memos_sql = """
        CREATE TABLE IF NOT EXISTS credit_memos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            credit_id VARCHAR(50) NOT NULL UNIQUE,
            order_id VARCHAR(50) NOT NULL,
            increment_id VARCHAR(50),
            amount DECIMAL(10, 4),
            created_date DATETIME,
            updated_date DATETIME,
            sync_history_id INT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sync_history_id) REFERENCES sync_history(id),
            INDEX idx_credit_id (credit_id),
            INDEX idx_order_id (order_id)
        )
        """
        
        try:
            cursor.execute(sync_history_sql)
            cursor.execute(processed_orders_sql)
            cursor.execute(credit_memos_sql)
            self.connection.commit()
            logger.info("Database tables created successfully")
        except Error as e:
            logger.error(f"Error creating tables: {e}")
            raise
        finally:
            cursor.close()
            
    def get_last_sync_date(self, sync_type: str = 'orders') -> Optional[datetime]:
        """Get the last successful sync date."""
        cursor = self.connection.cursor()
        query = """
        SELECT last_order_date 
        FROM sync_history 
        WHERE sync_type = %s AND status = 'success' 
        ORDER BY sync_end DESC 
        LIMIT 1
        """
        
        try:
            cursor.execute(query, (sync_type,))
            result = cursor.fetchone()
            if result and result[0]:
                return result[0]
            return None
        except Error as e:
            logger.error(f"Error fetching last sync date: {e}")
            return None
        finally:
            cursor.close()
            
    def start_sync(self, sync_type: str = 'orders') -> int:
        """Record the start of a sync operation."""
        cursor = self.connection.cursor()
        query = """
        INSERT INTO sync_history (sync_type, sync_start, status) 
        VALUES (%s, %s, %s)
        """
        
        try:
            cursor.execute(query, (sync_type, datetime.now(), 'running'))
            self.connection.commit()
            return cursor.lastrowid
        except Error as e:
            logger.error(f"Error starting sync record: {e}")
            raise
        finally:
            cursor.close()
            
    def end_sync(self, sync_id: int, status: str, orders_processed: int = 0, 
                 credits_processed: int = 0, last_order_date: Optional[datetime] = None,
                 error_message: Optional[str] = None):
        """Record the end of a sync operation."""
        cursor = self.connection.cursor()
        query = """
        UPDATE sync_history 
        SET sync_end = %s, status = %s, orders_processed = %s, 
            credits_processed = %s, last_order_date = %s, error_message = %s
        WHERE id = %s
        """
        
        try:
            cursor.execute(query, (datetime.now(), status, orders_processed, 
                                 credits_processed, last_order_date, error_message, sync_id))
            self.connection.commit()
        except Error as e:
            logger.error(f"Error ending sync record: {e}")
            raise
        finally:
            cursor.close()
            
    def save_order(self, order: Dict[str, Any], sync_id: int):
        """Save an order to the database."""
        cursor = self.connection.cursor()
        
        # Check if order already exists
        check_query = "SELECT id FROM processed_orders WHERE order_id = %s"
        cursor.execute(check_query, (order['Id'],))
        
        if cursor.fetchone():
            # Update existing order
            update_query = """
            UPDATE processed_orders 
            SET order_number = %s, order_date = %s, last_updated = %s,
                total_amount = %s, currency_code = %s, status = %s,
                sync_history_id = %s
            WHERE order_id = %s
            """
            cursor.execute(update_query, (
                order['OrderNumber'],
                order['Date'],
                order['LastUpdatedDate'],
                float(order['Total']),
                order['CurrencyCode'],
                order['Status'],
                sync_id,
                order['Id']
            ))
        else:
            # Insert new order
            insert_query = """
            INSERT INTO processed_orders 
            (order_id, order_number, order_date, last_updated, total_amount, 
             currency_code, status, sync_history_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (
                order['Id'],
                order['OrderNumber'],
                order['Date'],
                order['LastUpdatedDate'],
                float(order['Total']),
                order['CurrencyCode'],
                order['Status'],
                sync_id
            ))
            
        # Save credit memos if any
        if 'Credits' in order and order['Credits']:
            for credit in order['Credits']:
                self.save_credit_memo(credit, order['Id'], sync_id)
                
        cursor.close()
        
    def save_credit_memo(self, credit: Dict[str, Any], order_id: str, sync_id: int):
        """Save a credit memo to the database."""
        cursor = self.connection.cursor()
        
        # Check if credit memo already exists
        check_query = "SELECT id FROM credit_memos WHERE credit_id = %s"
        cursor.execute(check_query, (credit['entity_id'],))
        
        if not cursor.fetchone():
            insert_query = """
            INSERT INTO credit_memos 
            (credit_id, order_id, increment_id, amount, created_date, 
             updated_date, sync_history_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (
                credit['entity_id'],
                order_id,
                credit.get('increment_id'),
                float(credit['grand_total']),
                credit['created_at'],
                credit['updated_at'],
                sync_id
            ))
            
        cursor.close()
        
    def commit(self):
        """Commit current transaction."""
        if self.connection:
            self.connection.commit()
            
    def rollback(self):
        """Rollback current transaction."""
        if self.connection:
            self.connection.rollback()
