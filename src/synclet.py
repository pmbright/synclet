"""Main Synclet application for syncing Magento orders to QuickBooks Online."""

import logging
import sys
import time
import yaml
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any

from database import DatabaseManager
from magento_client import MagentoClient


class Synclet:
    """Main application class for Synclet."""
    
    def __init__(self, config_path: str):
        """Initialize Synclet with configuration."""
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.db_manager = DatabaseManager(self.config['database'])
        self.magento_client = MagentoClient(self.config['magento'])
        
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Error loading configuration: {e}")
            sys.exit(1)
            
    def _setup_logging(self):
        """Set up logging configuration."""
        log_config = self.config['logging']
        log_file = Path(log_config['log_file'])
        
        # Create logs directory if it doesn't exist
        log_file.parent.mkdir(exist_ok=True)
        
        # Configure logger
        logger = logging.getLogger()
        logger.setLevel(getattr(logging, log_config['level']))
        
        # File handler with rotation
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=log_config['max_size_mb'] * 1024 * 1024,
            backupCount=log_config['backup_count']
        )
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
        
        logging.info("Logging initialized")
        
    def initialize_database(self):
        """Connect to database and create tables."""
        logging.info("Initializing database...")
        
        if not self.db_manager.connect():
            logging.error("Failed to connect to database")
            sys.exit(1)
            
        try:
            self.db_manager.create_tables()
            logging.info("Database initialized successfully")
        except Exception as e:
            logging.error(f"Failed to create tables: {e}")
            sys.exit(1)
            
    def sync_orders(self):
        """Perform a single sync operation."""
        logging.info("Starting order sync...")
        
        sync_id = None
        try:
            # Start sync record
            sync_id = self.db_manager.start_sync('orders')
            
            # Determine sync date
            last_sync = self.db_manager.get_last_sync_date('orders')
            
            if last_sync:
                # Use last updated time for incremental sync
                sync_date = last_sync.strftime('%Y-%m-%dT%H:%M:%S')
                logging.info(f"Incremental sync from: {sync_date}")
                response = self.magento_client.fetch_orders(last_updated_time=sync_date)
            else:
                # Initial sync
                initial_date = self.config['sync']['initial_sync_date']
                logging.info(f"Initial sync from: {initial_date}")
                response = self.magento_client.fetch_orders(order_created_time=initial_date)
                
            # Parse orders
            orders = self.magento_client.parse_orders(response)
            
            # Process each order
            orders_processed = 0
            credits_processed = 0
            
            for order in orders:
                try:
                    self.db_manager.save_order(order, sync_id)
                    orders_processed += 1
                    
                    # Count credit memos
                    if 'Credits' in order:
                        credits_processed += len(order['Credits'])
                        
                except Exception as e:
                    logging.error(f"Error saving order {order['Id']}: {e}")
                    # Continue with other orders
                    
            # Commit all changes
            self.db_manager.commit()
            
            # Get latest order date
            latest_date = self.magento_client.get_latest_order_date(orders)
            
            # End sync record
            self.db_manager.end_sync(
                sync_id,
                'success',
                orders_processed=orders_processed,
                credits_processed=credits_processed,
                last_order_date=latest_date
            )
            
            logging.info(f"Sync completed. Orders: {orders_processed}, Credits: {credits_processed}")
            
        except Exception as e:
            logging.error(f"Sync failed: {e}")
            
            # Rollback database changes
            self.db_manager.rollback()
            
            # Update sync record if it was created
            if sync_id:
                self.db_manager.end_sync(
                    sync_id,
                    'failed',
                    error_message=str(e)
                )
                
    def run_once(self):
        """Run a single sync operation."""
        self.initialize_database()
        self.sync_orders()
        self.db_manager.disconnect()
        
    def run_continuous(self):
        """Run continuous sync based on configured interval."""
        self.initialize_database()
        
        interval = self.config['sync']['interval_minutes'] * 60
        logging.info(f"Starting continuous sync every {self.config['sync']['interval_minutes']} minutes")
        
        try:
            while True:
                self.sync_orders()
                logging.info(f"Sleeping for {self.config['sync']['interval_minutes']} minutes...")
                time.sleep(interval)
                
        except KeyboardInterrupt:
            logging.info("Sync interrupted by user")
        finally:
            self.db_manager.disconnect()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Synclet - Magento to QuickBooks sync tool')
    parser.add_argument(
        '--config',
        default='config/config.yaml',
        help='Path to configuration file'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run sync once and exit'
    )
    
    args = parser.parse_args()
    
    # Create Synclet instance
    synclet = Synclet(args.config)
    
    # Run sync
    if args.once:
        synclet.run_once()
    else:
        synclet.run_continuous()


if __name__ == '__main__':
    main()
