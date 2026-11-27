#!/usr/bin/env python3
import click
import sys
from datetime import datetime, timedelta
import json
from database import Database
from magento_api import MagentoAPI
from config import EXPORT_ON_SYNC


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
        self.db.create_stock_sync_tables()

    def _update_database_schema(self):
        """Update database schema to support metadata storage"""
        try:
            cursor = self.db.connection.cursor()

            # Add metadata column if it doesn't exist
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'sync_history'
                AND COLUMN_NAME = 'metadata'
            """)

            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    ALTER TABLE sync_history
                    ADD COLUMN metadata JSON
                    COMMENT 'Stores sync parameters like order_created_time and last_updated_time'
                """)
                self.db.connection.commit()
                print("Added metadata column to sync_history table")

            # Update sync_type enum to include 'catch-up' if needed
            cursor.execute("""
                SELECT COLUMN_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'sync_history'
                AND COLUMN_NAME = 'sync_type'
            """)

            column_type = cursor.fetchone()[0]
            if "'catch-up'" not in column_type:
                cursor.execute("""
                    ALTER TABLE sync_history
                    MODIFY COLUMN sync_type ENUM('initial', 'incremental', 'catch-up') NOT NULL
                """)
                self.db.connection.commit()
                print("Updated sync_type enum to include 'catch-up'")

        except Exception as e:
            print(f"Warning: Could not update database schema: {e}")

    def _get_order_created_time(self):
        """
        Get the OrderCreatedTime for sync based on OneSaas sliding window strategy
        Shifts forward periodically to maintain a reasonable window size
        """
        try:
            cursor = self.db.connection.cursor()
            cursor.execute("""
                SELECT metadata
                FROM sync_history
                WHERE metadata IS NOT NULL
                AND JSON_EXTRACT(metadata, '$.order_created_time') IS NOT NULL
                ORDER BY sync_time DESC
                LIMIT 1
            """)
            result = cursor.fetchone()

            if result and result[0]:
                metadata = json.loads(result[0]) if isinstance(result[0], str) else result[0]
                stored_date = metadata.get('order_created_time', '2025-01-02 00:00:00')
            else:
                stored_date = '2025-01-02 00:00:00'

            stored_dt = datetime.strptime(stored_date, '%Y-%m-%d %H:%M:%S')
            current_dt = datetime.now()
            days_diff = (current_dt - stored_dt).days

            if days_diff >= 180:
                windows_to_slide = days_diff // 180
                days_to_add = windows_to_slide * 3
                new_dt = stored_dt + timedelta(days=days_to_add)
                new_date = new_dt.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[SLIDING WINDOW] Moving OrderCreatedTime forward:")
                print(f"  From: {stored_date}")
                print(f"  To: {new_date}")
                print(f"  Reason: {days_diff} days since window start (sliding every 180 days)")
                return new_date
            else:
                print(f"[WINDOW CHECK] Current window still valid ({days_diff}/180 days)")
                return stored_date

        except Exception as e:
            print(f"Warning: Could not retrieve stored OrderCreatedTime: {e}")
            return '2025-01-02 00:00:00'

    def sync_orders(self, force_initial=False, catch_up=False, limit=None, order_id=None, debug=False):
        """
        Sync orders from Magento using OneSaas polling strategy

        Args:
            force_initial: Force initial sync even if we have sync history
            catch_up: Perform catch-up sync from last OneSaas poll
            limit: Limit number of invoices exported to QBO
            order_id: Export only this Magento order number to QBO
            debug: Enable QBO exporter debug output
        """
        self.connect_db()
        start_time = datetime.now()
        print(f"\n=== Sync started at {start_time.strftime('%Y-%m-%d %H:%M:%S')} ===")

        try:
            ONESAAS_LAST_CREATED_TIME = '2025-01-02 00:00:00'
            ONESAAS_LAST_UPDATED_TIME = '2025-07-01 20:55:21'
            ONESAAS_LAST_CREATED_TIME = '2026-01-01 00:00:00'
            ONESAAS_LAST_UPDATED_TIME = '2026-05-20 08:52:31'

            if catch_up:
                sync_type = 'catch-up'
                order_created_time = ONESAAS_LAST_CREATED_TIME
                last_updated_time = ONESAAS_LAST_UPDATED_TIME
                print(f"[CATCH-UP SYNC] Starting from OneSaas last position:")
                print(f"  OrderCreatedTime: {order_created_time}")
                print(f"  LastUpdatedTime: {last_updated_time}")
            else:
                last_sync = self.db.get_last_sync_time() if not force_initial else None

                if last_sync:
                    sync_type = 'incremental'

                    current_time = datetime.now()
                    two_hours_ago = current_time - timedelta(hours=2)
                    last_updated_time = two_hours_ago.strftime('%Y-%m-%d %H:%M:%S')

                    order_created_time = self._get_order_created_time()

                    print(f"[INCREMENTAL SYNC]")
                    print(f"  OrderCreatedTime: {order_created_time}")
                    print(f"  LastUpdatedTime: {last_updated_time} (2-hour lag)")
                else:
                    sync_type = 'initial'
                    order_created_time = ONESAAS_LAST_CREATED_TIME
                    last_updated_time = ONESAAS_LAST_UPDATED_TIME
                    print(f"[INITIAL SYNC] Starting from OneSaas last position")
                    print(f"  OrderCreatedTime: {order_created_time}")
                    print(f"  LastUpdatedTime: {last_updated_time}")

            orders = self.api.fetch_all_orders(
                order_created_time=order_created_time,
                last_updated_time=last_updated_time
            )

            if orders is None:
                print("Failed to fetch orders")
                self.db.record_sync(sync_type, 0, success=False, error_message="Failed to fetch orders")
                return False

            print(f"\n[SYNC RESULT] Found {len(orders)} orders to sync")

            successful_saves = 0
            last_order_time = None

            for order in orders:
                if self.db.save_order(order):
                    successful_saves += 1

                    order_time = datetime.strptime(order['LastUpdatedDate'], '%Y-%m-%d %H:%M:%S')
                    if not last_order_time or order_time > last_order_time:
                        last_order_time = order_time

            print(f"[SAVE RESULT] Successfully saved {successful_saves} out of {len(orders)} orders")

            metadata = {
                'order_created_time': order_created_time,
                'last_updated_time': last_updated_time,
                'sync_timestamp': datetime.now().isoformat()
            }

            self._record_sync_with_metadata(
                sync_type=sync_type,
                orders_synced=successful_saves,
                last_order_time=last_order_time,
                success=True,
                metadata=metadata
            )

            if EXPORT_ON_SYNC:
                print("\n[QBO EXPORT] Exporting orders to QuickBooks...")

                from qbo_exporter import QBOExporter
                from config import QBO_CONFIG, QBO_DEBUG

                exporter = QBOExporter(
                    self.db.connection,
                    config={"qbo": QBO_CONFIG},
                    debug=debug or QBO_DEBUG
                )

                exporter.export_orders(
                    order_ids=[order_id] if order_id else None,
                    limit=limit,
                    export_complete_only=True
                )

            return True

        except Exception as e:
            print(f"[ERROR] During sync: {e}")
            self.db.record_sync(sync_type, 0, success=False, error_message=str(e))
            return False

        finally:
            end_time = datetime.now()
            duration = end_time - start_time
            print(f"=== Sync finished at {end_time.strftime('%Y-%m-%d %H:%M:%S')} ===")
            print(f"=== Duration: {duration} ===\n")
            self.db.disconnect()

    def _record_sync_with_metadata(self, sync_type, orders_synced, last_order_time, success, metadata):
        """Record sync with metadata support"""
        try:
            cursor = self.db.connection.cursor()

            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'sync_history'
                AND COLUMN_NAME = 'metadata'
            """)

            has_metadata_column = cursor.fetchone()[0] > 0

            if has_metadata_column:
                cursor.execute("""
                    INSERT INTO sync_history
                    (sync_time, sync_type, orders_synced, last_order_time, success, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    datetime.now(),
                    sync_type,
                    orders_synced,
                    last_order_time,
                    success,
                    json.dumps(metadata)
                ))
            else:
                self.db.record_sync(sync_type, orders_synced, last_order_time, success)

            self.db.connection.commit()

        except Exception as e:
            print(f"Warning: Could not record sync with metadata: {e}")
            self.db.record_sync(sync_type, orders_synced, last_order_time, success)

    def clear_database(self):
        """Drop all tables from the database"""
        self.connect_db()

        try:
            if click.confirm("This will DROP ALL TABLES in the database. Are you sure?", abort=True):
                self.db.clear_all_data()
                print("All tables dropped successfully. Run 'init' to recreate them.")
        finally:
            self.db.disconnect()

    def test_connection(self):
        """Test connections to Magento API and database"""
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
    """Synclet - Magento order sync and QBO export tool"""
    pass


@cli.command()
@click.option('--force-initial', is_flag=True, help='Force initial sync from configured date')
@click.option('--catch-up', is_flag=True, help='Catch up from last OneSaas sync position')
@click.option('--limit', type=int, help='Limit number of invoices exported to QBO')
@click.option('--order-id', type=str, help='Export a specific Magento order number to QBO (e.g. 000119667)')
@click.option('--debug', is_flag=True, help='Enable QBO exporter debug output')
def sync(force_initial, catch_up, limit, order_id, debug):
    """Sync orders from Magento and export to QBO"""
    synclet = Synclet()
    success = synclet.sync_orders(
        force_initial=force_initial,
        catch_up=catch_up,
        limit=limit,
        order_id=order_id,
        debug=debug
    )
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
    synclet._update_database_schema()
    print("Database tables initialized")
    synclet.db.disconnect()


@cli.command()
def status():
    """Show sync status and statistics"""
    synclet = Synclet()
    synclet.connect_db()

    try:
        cursor = synclet.db.connection.cursor()

        cursor.execute("""
            SELECT sync_time, sync_type, orders_synced, last_order_time, success, metadata
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

            if last_sync[5]:
                try:
                    metadata = json.loads(last_sync[5]) if isinstance(last_sync[5], str) else last_sync[5]
                    print(f"\nSync Parameters:")
                    print(f"  OrderCreatedTime: {metadata.get('order_created_time', 'N/A')}")
                    print(f"  LastUpdatedTime: {metadata.get('last_updated_time', 'N/A')}")
                except Exception:
                    pass
        else:
            print("No sync history found")

        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]
        print(f"\nTotal orders in database: {total_orders}")

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

        cursor.execute("""
            SELECT sync_time, products_synced, success
            FROM stock_sync_history
            ORDER BY sync_time DESC
            LIMIT 1
        """)
        last_stock_sync = cursor.fetchone()

        if last_stock_sync:
            print(f"\nLast stock sync: {last_stock_sync[0]}")
            print(f"Products synced: {last_stock_sync[1]}")
            print(f"Success: {'Yes' if last_stock_sync[2] else 'No'}")

    finally:
        synclet.db.disconnect()


@cli.command()
@click.option('--order-id', type=int, help='Export specific order by internal DB ID')
@click.option('--start-date', help='Start date for export (YYYY-MM-DD)')
@click.option('--end-date', help='End date for export (YYYY-MM-DD)')
@click.option('--simple', is_flag=True, help='Use simple format (line items only)')
@click.option('--all-statuses', is_flag=True, help='Export all orders regardless of status')
@click.option('--no-tracking', is_flag=True, help='Disable export tracking')
def export_csv(order_id, start_date, end_date, simple, all_statuses, no_tracking):
    """Export orders to CSV invoice files"""
    synclet = Synclet()
    synclet.connect_db()
    synclet._update_database_schema()

    try:
        from csv_exporter_v2 import CSVExporter
        from config import CSV_EXPORT_COMPLETE_ONLY, CSV_EXPORT_TRACK

        exporter = CSVExporter(synclet.db.connection)

        order_ids = [order_id] if order_id else None
        export_complete_only = CSV_EXPORT_COMPLETE_ONLY and not all_statuses
        track_exports = CSV_EXPORT_TRACK and not no_tracking

        exporter.export_orders(
            order_ids=order_ids,
            start_date=start_date,
            end_date=end_date,
            simple_format=simple,
            export_complete_only=export_complete_only,
            track_exports=track_exports
        )

    except Exception as e:
        print(f"Error exporting orders: {e}")
    finally:
        synclet.db.disconnect()


@cli.command()
def clear_csv_history():
    """Clear CSV export history (for testing/retries)"""
    synclet = Synclet()
    synclet.connect_db()
    synclet._update_database_schema()

    try:
        if hasattr(synclet.db, 'clear_csv_export_history'):
            synclet.db.clear_csv_export_history()
        else:
            cursor = synclet.db.connection.cursor()
            cursor.execute("TRUNCATE TABLE csv_export_history")
            synclet.db.connection.commit()
            print("CSV export history cleared")

    except Exception as e:
        print(f"Error clearing CSV export history: {e}")
    finally:
        synclet.db.disconnect()


if __name__ == '__main__':
    cli()
