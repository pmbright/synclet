# QuickBooks Integration Quick Start Guide

## Setting up QuickBooks OAuth2

1. **Create a QuickBooks App**
   - Go to https://developer.intuit.com
   - Create a new app for QuickBooks Online
   - Select "Accounting" scope
   - Set redirect URI to `http://localhost:8080/callback`

2. **Configure Credentials**
   
   Add to your `config.py` or `.env` file:
   ```
   QBO_CLIENT_ID=your_client_id_here
   QBO_CLIENT_SECRET=your_client_secret_here
   QBO_ENVIRONMENT=sandbox  # Use 'production' for live data
   QBO_COMPANY_ID=your_realm_id_here  # Optional, will be set during auth
   ```

3. **Authorize QuickBooks Access**
   
   Run the authorization command:
   ```bash
   python synclet.py qbo-auth
   ```
   
   This will:
   - Open your browser for QuickBooks login
   - Ask you to authorize the app
   - Save the access tokens locally

4. **Test the Connection**
   
   ```bash
   python synclet.py test
   ```
   
   You should see:
   - ✓ Magento API connection successful
   - ✓ Database connection successful
   - ✓ QuickBooks connection successful

## Using Stock Sync

Once authorized, sync stock levels from QuickBooks to Magento:

```bash
python synclet.py stock-sync
```

This will:
1. Fetch all inventory items from QuickBooks
2. Match them by SKU to Magento products
3. Update stock levels in Magento
4. Save sync history in the database

## Stock Sync Details

The sync process:
- Uses QuickBooks SKU field to match Magento ProductCode
- Updates StockAtHand and StockAvailable in Magento
- Sets StockAllocated to 0 (QuickBooks doesn't track this)
- Records all syncs in `stock_sync_history` table
- Maintains product mappings for tracking

## Scheduling Stock Sync

Add to crontab for hourly stock sync:
```bash
# Sync stock levels every hour
30 * * * * /usr/bin/python3 /path/to/synclet.py stock-sync >> /var/log/synclet-stock.log 2>&1
```

## Automatic CSV Export on Sync

Enable automatic CSV export after each order sync:

```bash
# In .env or environment variables
CSV_EXPORT_ON_SYNC=true
CSV_EXPORT_DIR=exports  # Optional, defaults to 'exports'
```

When enabled:
- CSV files are automatically created after each successful sync
- Only new/updated orders are exported during incremental syncs
- Files are saved in timestamped directories: `sync-YYYYMMDD-HHMMSS/`
- The export won't fail the sync if there's an error

Check if CSV export is enabled:
```bash
python synclet.py status
```

## Troubleshooting

1. **"QuickBooks connection failed"**
   - Run `python synclet.py qbo-auth` to re-authorize
   - Check credentials are correct
   - Ensure redirect URI matches app settings

2. **"No products with SKUs found"**
   - Ensure QuickBooks items have SKUs set
   - SKUs must match Magento product codes

3. **"Failed to update stock in Magento"**
   - Check Magento API has updateStock action
   - Verify access key permissions
   - Check Magento logs for details
