#!/usr/bin/env python3
import click
import sys
from datetime import datetime
from database import Database
from magento_api import MagentoAPI
from config import INITIAL_SYNC_DATE

class Synclet:
    def __init__(self):
        self.db = Database()
        self.api = MagentoAPI()
        
    def connect_db(self):
        """Connect to database and ensure tables exist"""
        if not self.db.connect():
            print("Failed to connect to database")
            sys.exit(1)
            
        # Create tables if they don't exist
        self.db.create_tables()
        
    def sync_orders(self, force_initial=False):
        """
        Sync orders from Magento
        
        Args:
            force_initial: Force initial sync even if we have sync history
        """
        self.connect_db()
        
        try:
            # Determine sync type
            last_sync = self.db.get_last_sync_time() if not force_initial else None
            
            if last_sync:
                # Incremental sync
                sync_type = 'incremental'
                sync_time_str = last_sync.strftime('%Y-%m-%d %H:%M:%S')
                print(f"Performing incremental sync from: {sync_time_str}")
                orders = self.api.fetch_all_orders(last_updated_time=sync_time_str)
            else:
                # Initial sync
                sync_type = 'initial'
                print(f"Performing initial sync from: {INITIAL_SYNC_DATE}")
                orders = self.api.fetch_all_orders(order_created_time=INITIAL_SYNC_DATE)
            
            if orders is None:
                print("Failed to fetch orders")
                self.db.record_sync(sync_type, 0, success=False, error_message="Failed to fetch orders")
                return False
                
            print(f"Found {len(orders)} orders to sync")
            
            # Save orders to database
            successful_saves = 0
            last_order_time = None
            
            for order in orders:
                if self.db.save_order(order):
                    successful_saves += 1
                    
                    # Track the latest order time
                    order_time = datetime.strptime(order['LastUpdatedDate'], '%Y-%m-%d %H:%M:%S')
                    if not last_order_time or order_time > last_order_time:
                        last_order_time = order_time
                        
            print(f"Successfully saved {successful_saves} out of {len(orders)} orders")
            
            # Record sync history
            self.db.record_sync(
                sync_type=sync_type,
                orders_synced=successful_saves,
                last_order_time=last_order_time,
                success=True
            )
            
            return True
            
        except Exception as e:
            print(f"Error during sync: {e}")
            self.db.record_sync(sync_type, 0, success=False, error_message=str(e))
            return False
            
        finally:
            self.db.disconnect()
            
    def clear_database(self):
        """Clear all data from the database"""
        self.connect_db()
        
        try:
            # Ask for confirmation
            if click.confirm("This will delete ALL data in the database. Are you sure?", abort=True):
                self.db.clear_all_data()
                print("Database cleared successfully")
                
        finally:
            self.db.disconnect()
            
    def test_connection(self):
        """Test connections to both Magento API and database"""
        print("Testing Magento API connection...")
        if self.api.test_connection():
            print("✓ Magento API connection successful")
        else:
            print("✗ Magento API connection failed")
            
        print("\nTesting database connection...")
        if self.db.connect():
            print("✓ Database connection successful")
            self.db.disconnect()
        else:
            print("✗ Database connection failed")

@click.group()
def cli():
    """Synclet - Magento to QuickBooks Order Sync Tool"""
    pass

@cli.command()
@click.option('--force-initial', is_flag=True, help='Force initial sync from configured date')
def sync(force_initial):
    """Sync orders from Magento"""
    synclet = Synclet()
    success = synclet.sync_orders(force_initial=force_initial)
    sys.exit(0 if success else 1)

@cli.command()
def clear():
    """Clear all data from the database"""
    synclet = Synclet()
    synclet.clear_database()

@cli.command()
def test():
    """Test API and database connections"""
    synclet = Synclet()
    synclet.test_connection()

@cli.command()
def init():
    """Initialize database tables"""
    synclet = Synclet()
    synclet.connect_db()
    print("Database tables initialized")
    synclet.db.disconnect()

@cli.command()
def status():
    """Show sync status and statistics"""
    synclet = Synclet()
    synclet.connect_db()
    
    try:
        # Get last sync info
        cursor = synclet.db.connection.cursor()
        
        # Last sync
        cursor.execute("""
            SELECT sync_time, sync_type, orders_synced, last_order_time, success
            FROM sync_history
            ORDER BY sync_time DESC
            LIMIT 1
        """)
        last_sync = cursor.fetchone()
        
        if last_sync:
            print(f"Last sync: {last_sync[0]} ({last_sync[1]})")
            print(f"Orders synced: {last_sync[2]}")
            print(f"Last order time: {last_sync[3]}")
            print(f"Success: {'Yes' if last_sync[4] else 'No'}")
        else:
            print("No sync history found")
            
        # Total orders
        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]
        print(f"\nTotal orders in database: {total_orders}")
        
        # Recent orders
        cursor.execute("""
            SELECT order_number, order_date, total, status
            FROM orders
            ORDER BY last_updated_date DESC
            LIMIT 5
        """)
        recent_orders = cursor.fetchall()
        
        if recent_orders:
            print("\nRecent orders:")
            for order in recent_orders:
                print(f"  {order[0]} - {order[1]} - £{order[2]} - {order[3]}")
                
    finally:
        synclet.db.disconnect()

if __name__ == '__main__':
    cli()
