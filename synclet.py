#!/usr/bin/env python3
"""Run one Magento-to-QuickBooks invoice sync, then exit (scheduled by cron)."""
import argparse
from datetime import datetime, timedelta
import json

from config import EXPORT_ON_SYNC, QBO_CONFIG, QBO_DEBUG
from database import Database
from magento_api import MagentoAPI

INITIAL_CREATED_TIME = '2026-01-01 00:00:00'
INITIAL_UPDATED_TIME = '2026-05-20 08:52:31'
FALLBACK_CREATED_TIME = '2025-01-02 00:00:00'


class Synclet:
    def __init__(self):
        self.db = Database()
        self.api = MagentoAPI()

    def _get_order_created_time(self):
        """Keep the existing sliding window and historical metadata fallback."""
        try:
            cursor = self.db.connection.cursor()
            cursor.execute("""
                SELECT metadata FROM sync_history
                WHERE metadata IS NOT NULL
                AND JSON_EXTRACT(metadata, '$.order_created_time') IS NOT NULL
                ORDER BY sync_time DESC LIMIT 1
            """)
            result = cursor.fetchone()
            stored_date = FALLBACK_CREATED_TIME
            if result and result[0]:
                metadata = json.loads(result[0]) if isinstance(result[0], str) else result[0]
                stored_date = metadata.get('order_created_time', FALLBACK_CREATED_TIME)
            stored_dt = datetime.strptime(stored_date, '%Y-%m-%d %H:%M:%S')
            days_diff = (datetime.now() - stored_dt).days
            if days_diff >= 180:
                new_dt = stored_dt + timedelta(days=(days_diff // 180) * 3)
                new_date = new_dt.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[SLIDING WINDOW] OrderCreatedTime: {stored_date} -> {new_date}")
                return new_date
            return stored_date
        except Exception as error:
            print(f"Warning: Could not retrieve stored OrderCreatedTime: {error}")
            return FALLBACK_CREATED_TIME

    def _record_sync_with_metadata(self, sync_type, orders_synced, last_order_time, metadata):
        """Support existing databases both with and without the metadata column."""
        try:
            cursor = self.db.connection.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'sync_history' AND COLUMN_NAME = 'metadata'
            """)
            if cursor.fetchone()[0] > 0:
                cursor.execute("""
                    INSERT INTO sync_history
                    (sync_time, sync_type, orders_synced, last_order_time, success, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (datetime.now(), sync_type, orders_synced, last_order_time, True, json.dumps(metadata)))
                self.db.connection.commit()
                return
        except Exception as error:
            print(f"Warning: Could not record sync with metadata: {error}")
        self.db.record_sync(sync_type, orders_synced, last_order_time, True)

    def sync_orders(self):
        """Import Magento orders and export eligible, previously unexported invoices."""
        start_time = datetime.now()
        print(f"\n=== Sync started at {start_time:%Y-%m-%d %H:%M:%S} ===")
        sync_type = 'initial'
        connected = False
        try:
            connected = self.db.connect()
            if not connected:
                raise RuntimeError('Failed to connect to database')
            if not self.db.create_tables():
                raise RuntimeError('Failed to prepare database tables')

            if self.db.get_last_sync_time():
                sync_type = 'incremental'
                last_updated_time = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
                order_created_time = self._get_order_created_time()
            else:
                order_created_time = INITIAL_CREATED_TIME
                last_updated_time = INITIAL_UPDATED_TIME
            print(f"[{sync_type.upper()} SYNC] OrderCreatedTime: {order_created_time}; LastUpdatedTime: {last_updated_time}")
            orders = self.api.fetch_all_orders(
                order_created_time=order_created_time, last_updated_time=last_updated_time)
            if orders is None:
                raise RuntimeError('Failed to fetch all Magento order pages')

            last_order_time = None
            for order in orders:
                if not self.db.save_order(order):
                    raise RuntimeError(f"Failed to save order {order.get('OrderNumber', 'unknown')}")
                order_time = datetime.strptime(order['LastUpdatedDate'], '%Y-%m-%d %H:%M:%S')
                last_order_time = max(last_order_time, order_time) if last_order_time else order_time
            print(f"[SYNC RESULT] Saved {len(orders)} orders")

            # Keep import metadata separate from QBO's per-invoice export ledger.
            self._record_sync_with_metadata(sync_type, len(orders), last_order_time, {
                'order_created_time': order_created_time,
                'last_updated_time': last_updated_time,
                'sync_timestamp': datetime.now().isoformat(),
            })
            if EXPORT_ON_SYNC:
                from qbo_exporter import QBOExporter
                exporter = QBOExporter(self.db.connection, config={'qbo': QBO_CONFIG}, debug=QBO_DEBUG)
                exporter.export_orders()
            return True
        except Exception as error:
            print(f"[ERROR] During sync: {error}")
            if connected:
                self.db.record_sync(sync_type, 0, success=False, error_message=str(error))
            return False
        finally:
            self.db.disconnect()
            end_time = datetime.now()
            print(f"=== Sync finished at {end_time:%Y-%m-%d %H:%M:%S}; duration: {end_time - start_time} ===\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sync'], help='Run one scheduled invoice sync')
    parser.parse_args(argv)
    return 0 if Synclet().sync_orders() else 1


if __name__ == '__main__':
    raise SystemExit(main())
