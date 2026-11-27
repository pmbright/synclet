# Synclet

Synclet runs a Magento-to-QuickBooks invoice sync or pulls QuickBooks stock into
Magento, then exits. It is scheduled by cron; it is not a daemon or an installed
Python package.

```cron
0 * * * * cd /home/rocky/synclet && flock -n /tmp/synclet.lock /usr/bin/python synclet.py sync >> /home/rocky/synclet/logs/synclet.log 2>&1
```

The invoice command and external `flock` remain unchanged. Stock synchronization
is now part of this directory and is invoked separately with `stock-sync`.

## How a run works

1. Connect to MariaDB and ensure the order/history tables exist.
2. Fetch Magento orders through the installed OneSaas connector, including all pages.
3. Save orders and their related items, addresses, and credits.
4. Record import history and polling-window metadata.
5. Export eligible orders to QuickBooks and record each exported invoice.

The existing rules are retained:

- Initial polling starts with `OrderCreatedTime=2026-01-01 00:00:00` and
  `LastUpdatedTime=2026-05-20 08:52:31`, the effective dates in the original script.
- Incremental runs use a two-hour overlap and the existing sliding-window calculation.
  Existing databases without history metadata keep the previous fallback behaviour.
- Only `processing` and `complete` orders are exported, newest first.
- Both `qbo_export_history` and historical `csv_export_history` exclude previously
  exported orders. Keep both tables and their contents when updating an installation.
- Invoice numbering, customer, product-code corrections, VAT, discounts, shipping,
  line merging, and rounding reconciliation retain their existing behaviour.
- `EXPORT_ON_SYNC=false` imports Magento orders without exporting to QuickBooks.

Exit status is 0 on success and 1 on a failed run. A failed Magento page, invalid
API response, or failed order save stops invoice export. The connector's JSON empty
string still counts as a valid end-of-pages response. URLs containing the Magento
access key are not logged.

## Files

| File | Purpose |
| --- | --- |
| `synclet.py` | The `sync` command and run orchestration |
| `config.py` | Environment configuration |
| `magento_api.py` | Magento order pagination through the OneSaas connector |
| `database.py` | Order storage and import history |
| `qbo_exporter.py` | QuickBooks invoice creation, reconciliation, and export history |
| `qbo_token_store.py` | Existing QuickBooks token refresh and persistence |
| `stock_sync.py` | QuickBooks inventory reads and Magento REST stock updates |
| `qbo_auth.py` | Manual authorization/recovery helper, formerly `qbo_auth_test.py` |
| `scripts/create_db.sql` | Optional initial database/user provisioning |
| `tests/` | Offline regression tests with synthetic data |

## Setup

Use Python 3.8+ with the three dependencies in `requirements.txt` installed in the
interpreter used by cron. The existing server uses Python 3.12.9. A new environment
can be created with:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
mkdir -p logs
```

Fill in `.env`. On an existing installation retain the existing `.env` and
`qbo_tokens.json`; do not replace them with example values. Environment variables
already supplied by cron take precedence. Relative `QBO_TOKEN_FILE` paths resolve
against this application directory, as does `.env`.

The existing default QuickBooks customer is `15990`; set `QBO_CUSTOMER_ID` if a
separate installation uses another customer. QuickBooks product/service names must
match the Magento product codes, subject to the three existing corrections in the
invoice exporter. Tax and shipping/adjustment item names are configurable in `.env`.
`QBO_DEBUG=true` preserves the original detailed invoice logging; set it to `false`
for less output.

For a new database, provision the database/user with suitable permissions first.
The optional `scripts/create_db.sql` contains a password placeholder that must be
replaced before use. The scheduled command creates missing application tables;
there is no separate `init` command. Existing tables are not dropped or altered,
including old stock tables if present.

To authorize or recover the QuickBooks connection manually, the original
interactive helper is retained as:

```sh
python qbo_auth.py
```

It uses the existing localhost callback `http://localhost:8000/callback`; run it
where that callback and the browser can reach one another. It is not part of cron.
Credentials, tokens, logs, and generated data remain ignored by Git.

## Updating the existing cron installation

Retain the server's `.env`, token file, logs, database and existing export history.
Install `requirements.txt` using `/usr/bin/python`, the interpreter in the cron
entry. The `synclet.py sync` invocation requires no cron changes. The new version
has not been deployed to the server by this cleanup.

The old `src/` application, package/service installer, CSV exporter, stock scaffolding,
format-conversion utilities, and auxiliary CLI commands were removed. In particular,
`init`, `test`, `status`, `clear`, `export-csv`, `clear-csv-history`, and the old
`sync` options are no longer entry points. Their removal does not delete live data.
The original code remains in Git history.

## Stock synchronization

Stock uses the same QuickBooks connection, company and token file as the invoice
exporter. It reads all active QuickBooks `Inventory` items and matches `Sku` exactly
to Magento SKU. BBwear keeps the QuickBooks Product/Service Name and SKU equal to
Magento SKU; no separate mapping is needed. Only the QuickBooks SKU field is used
for stock matching, without the invoice exporter's three line-code corrections.

Stock uses Magento's standard legacy CatalogInventory REST API, matching this
site's configuration with multi-source inventory disabled. The invoice importer
continues to use the OneSaas connector. Set these in the existing private `.env`:

```dotenv
MAGENTO_REST_URL=https://www.bbwear.co.uk/rest/all/V1
MAGENTO_ACCESS_TOKEN=your_magento_integration_access_token
STOCK_REPORT_DIR=logs/stock-sync
```

Create and activate a Magento integration with `Magento_Catalog::catalog_inventory`
and `Magento_Catalog::products` permissions. On this site, Itoris renames the
top-level Catalog permission to Products, so the tree is Products > Inventory >
Products. This implementation sends a bearer token. Magento must have **Stores >
Configuration > Services > OAuth > Consumer Settings > Allow OAuth Access Tokens
to be used as standalone Bearer tokens** enabled for an integration token to work
in this mode. See [Magento's authentication guidance](https://developer.adobe.com/commerce/webapi/get-started/authentication/gs-authentication-token).
Keep this token separate from the existing OneSaas `MAGENTO_ACCESS_KEY`.

Preview all inventory or just selected SKUs:

```sh
python synclet.py stock-sync
python synclet.py stock-sync --sku BB-101-S
python synclet.py stock-sync --sku BB-101-S --sku BB-101-M
```

The default is a dry run: Magento is read but not changed. QuickBooks token refresh
may still update the shared local token file. Add `--apply` to write quantities:

```sh
flock -n /tmp/synclet.lock /usr/bin/python synclet.py stock-sync --apply
```

Behaviour:

- Copy `QtyOnHand` as an absolute stock quantity. Zero and negative values are
  included. Negative stock is out of stock unless backorders/thresholds permit it.
- Preserve Magento backorders, stock-management settings and minimum quantity
  thresholds. Skip parent products and products with stock management disabled.
- Fetch and validate the whole QuickBooks item list before any Magento write.
  Duplicate SKUs, repeated IDs or invalid quantities abort before updates. A
  requested `--sku` absent from active QBO inventory also aborts rather than zeroing it.
- Missing QuickBooks SKUs and unmatched Magento products are reported as errors.
  Other valid matches can still update. Products absent from QuickBooks are untouched.
- Write only changed quantities/statuses. Do not create products, invoices or stock
  tracking database tables. Uncertain write failures are reported without automatic replay.
- Create a timestamped CSV report of old/new quantities, statuses and results under
  `logs/stock-sync/`, with owner-only file permissions. Configure retention separately.
- Return 0 on success and 1 for failed/skipped mappings or API errors. The report
  and log indicate partial failures. A local report-directory lock prevents
  simultaneous stock commands using the same directory.

Use the **same external `/tmp/synclet.lock`** as invoices when scheduling stock.
That also serializes with the existing invoice deployment while it is still running
older token-store code. The updated token store locks refreshes and replaces token
files atomically; do not maintain a separate copy of the refresh token for stock.
All processes sharing a token file should use this token store, or the shared
external lock. Run manual authorization outside scheduled jobs.

### Stock and order timing

This copies QuickBooks on-hand quantity directly. Magento orders not yet reflected
in QuickBooks are **not subtracted again**. An order awaiting invoice export can
therefore have its Magento stock deduction temporarily overwritten. Running stock
only after a successful invoice sync reduces this window, but orders arriving
between the two snapshots can still be affected. This version does not claim to
calculate allocations for pending/unexported orders.

Once quantities have been checked and you choose to enable hourly updates, a
combined cron command can replace the invoice-only entry (it has not been installed):

```cron
0 * * * * cd /home/rocky/synclet && flock -n /tmp/synclet.lock /bin/sh -c '/usr/bin/python synclet.py sync && /usr/bin/python synclet.py stock-sync --apply' >> /home/rocky/synclet/logs/synclet.log 2>&1
```

Preview the CSV first, especially zero quantities and backorders, before enabling
`--apply` for all products. A missing Magento REST token fails before QuickBooks
authentication is attempted.

## Offline tests

```sh
python -m unittest discover -s tests -v
python synclet.py --help
```

Tests use synthetic orders and mocked HTTP/database calls. They do not contact
Magento or QuickBooks, change stock, or create real invoices. A normal `sync`
command is a live run, not a dry run. Stock has its own explicit preview/apply
behaviour. The old unused invoice `QBO_DRY_RUN` setting was removed.
