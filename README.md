# Synclet - Magento to QuickBooks Order Sync Tool

A Python-based tool to synchronize orders from Magento 2.4 to a local database, preparing them for later synchronization with QuickBooks Online.

## Features

- Fetch orders from Magento via custom API endpoint
- Store orders in MariaDB database with full details
- Track sync history for incremental updates
- Command-line interface for various operations
- Support for pagination to handle large order volumes
- Prepared for future QuickBooks Online integration

## Database Schema

The tool creates the following tables:
- `orders` - Main order information
- `order_items` - Line items for each order
- `order_addresses` - Billing and shipping addresses
- `order_credits` - Credit memo information
- `sync_history` - Track sync operations

## Installation

1. Clone the repository to your Rocky Linux server
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure the database connection and API credentials in `config.py` or create a `.env` file:
   ```
   MAGENTO_BASE_URL=https://www.bbwear.co.uk/onesaas_connect/index/index
   MAGENTO_ACCESS_KEY=your_access_key_here
   DB_HOST=localhost
   DB_PORT=3306
   DB_NAME=synclet_test
   DB_USER=synclet_test
   DB_PASSWORD=your_password_here
   INITIAL_SYNC_DATE=2025-07-01 07:41:16
   ```

## Usage

### Initialize Database
```bash
python synclet.py init
```

### Test Connections
```bash
python synclet.py test
```

### Sync Orders
For initial sync or regular sync:
```bash
python synclet.py sync
```

Force initial sync from configured date:
```bash
python synclet.py sync --force-initial
```

### Check Status
```bash
python synclet.py status
```

### Clear Database (Testing)
```bash
python synclet.py clear
```

## Sync Logic

1. **Initial Sync**: Uses `OrderCreatedTime` parameter to fetch all orders created after the specified date
2. **Incremental Sync**: Uses `LastUpdatedTime` parameter to fetch only orders created or updated since the last successful sync

## Magento API Parameters

The tool uses the following API parameters:
- `OrderCreatedTime` - For initial sync
- `LastUpdatedTime` - For incremental updates
- `PageSize` - Number of orders per page (default: 50)
- `Page` - Page number for pagination (0-based)
- `Action` - Always set to 'Orders'
- `AccessKey` - API authentication key

## Scheduling

For automated syncing, you can set up a cron job on your Rocky Linux server:

```bash
# Sync every hour
0 * * * * /usr/bin/python3 /path/to/synclet.py sync >> /var/log/synclet.log 2>&1
```

## Future QuickBooks Integration

The database schema is designed to support QuickBooks invoice reconstruction. Each order stores:
- Complete order details
- Individual line items with tax information
- Customer contact information
- Payment method details
- Credit memo information

## Development Notes

All files are created with Linux line endings (LF) for deployment to Rocky Linux servers.
