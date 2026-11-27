"""Pull QuickBooks quantities into Magento legacy inventory (MSI disabled)."""
import csv
import fcntl
import logging
import math
import os
from pathlib import Path
import time
from urllib.parse import quote, urlencode, urlsplit

import requests

from config import APP_DIR, QBO_CONFIG, STOCK_CONFIG

LOG = logging.getLogger("stock-sync")


class SyncError(Exception):
    pass


class ApiError(SyncError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"API returned HTTP {status}")


def number(value, label):
    if isinstance(value, bool):
        raise SyncError(f"Invalid {label}")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise SyncError(f"Invalid {label}") from None
    if not math.isfinite(result):
        raise SyncError(f"Non-finite {label}")
    return result


class Http:
    def __init__(self, timeout=60):
        self.timeout = timeout

    def request(self, method, url, token, body=None):
        for attempt in range(5):
            try:
                with requests.request(method, url, json=body, timeout=self.timeout,
                                      allow_redirects=False, headers={
                                          "Authorization": f"Bearer {token}",
                                          "Accept": "application/json",
                                      }) as response:
                    if 200 <= response.status_code < 300:
                        try:
                            return response.json()
                        except ValueError:
                            raise SyncError("API returned invalid JSON") from None
                    # Retry reads only. An uncertain stock write must be reviewed,
                    # not replayed after another Magento order could have arrived.
                    if method != "GET" or response.status_code not in (429, 500, 502, 503, 504) or attempt == 4:
                        raise ApiError(response.status_code)
                    retry = response.headers.get("Retry-After", "")
                    delay = min(float(retry), 30) if retry.isdigit() else 2 ** attempt
            except requests.RequestException:
                raise SyncError("API connection failed or timed out") from None
            LOG.warning("Temporary API failure; retrying in %ss", delay)
            time.sleep(delay)



class QuickBooks:
    def __init__(self, settings, http, token_store=None):
        if token_store is None:
            import qbo_token_store as token_store
        self.token_store, self.http = token_store, http
        tokens = token_store.get_valid_tokens()
        realm = str(tokens.get("realm_id") or tokens.get("realmId"))
        if not realm.isdigit():
            raise SyncError("QuickBooks company ID is missing or invalid")
        host = settings["base_url"].rstrip("/")
        if host not in ("https://quickbooks.api.intuit.com", "https://sandbox-quickbooks.api.intuit.com"):
            raise SyncError("QBO_BASE_URL must be a QuickBooks production or sandbox API URL")
        self.realm = realm
        self.base = f"{host}/v3/company/{realm}/query"
        self.token = tokens["access_token"]

    def inventory(self):
        items, start = [], 1
        while True:
            query = f"SELECT * FROM Item WHERE Type = 'Inventory' AND Active = true ORDERBY Id STARTPOSITION {start} MAXRESULTS 1000"
            url = self.base + "?" + urlencode({"query": query})
            try:
                page = self.http.request("GET", url, self.token)
            except ApiError as exc:
                if exc.status != 401:
                    raise
                tokens = self.token_store.get_valid_tokens(rejected_access_token=self.token)
                if str(tokens.get("realm_id") or tokens.get("realmId")) != self.realm:
                    raise SyncError("QuickBooks company changed during the run")
                self.token = tokens["access_token"]
                page = self.http.request("GET", url, self.token)
            if not isinstance(page, dict) or "Fault" in page or not isinstance(page.get("QueryResponse"), dict):
                raise SyncError("Invalid QuickBooks query response")
            batch = page["QueryResponse"].get("Item", [])
            if not isinstance(batch, list):
                raise SyncError("Invalid QuickBooks item list")
            items.extend(batch)
            if len(batch) < 1000:
                return items
            start += len(batch)


class Magento:
    def __init__(self, settings, http):
        self.http = http
        self.base = settings["rest_url"].rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise SyncError("magento.rest_url must be an HTTPS URL without credentials or query")
        self.token = os.environ.get(settings.get("token_env", "MAGENTO_ACCESS_TOKEN"), "")
        if not self.token:
            raise SyncError("Magento access token environment variable is empty")

    def get(self, path):
        return self.http.request("GET", self.base + path, self.token)

    def stock(self, sku):
        product = self.get("/products/" + quote(sku, safe=""))
        if not isinstance(product, dict) or not product.get("type_id"):
            raise SyncError("Invalid Magento product response")
        if product["type_id"] not in ("simple", "virtual", "downloadable"):
            return None
        stock = self.get("/stockItems/" + quote(sku, safe=""))
        if not isinstance(stock, dict) or not stock.get("item_id"):
            raise SyncError("Magento returned no stock item ID")
        return stock

    def update(self, sku, item_id, qty, in_stock):
        return self.http.request("PUT", self.base + "/products/" + quote(sku, safe="")
                                 + "/stockItems/" + str(int(item_id)), self.token,
                                 {"stockItem": {"qty": qty, "is_in_stock": in_stock}})


def plan_items(items):
    candidates, seen_ids, seen_skus = [], set(), set()
    missing = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("Id"):
            raise SyncError("QuickBooks returned an item without an ID")
        item_id = str(item["Id"])
        if item_id in seen_ids:
            raise SyncError(f"Repeated QuickBooks item ID {item_id}; rerun to obtain a consistent snapshot")
        seen_ids.add(item_id)
        if item.get("Type") != "Inventory" or item.get("Active") is False:
            continue
        sku = item.get("Sku")
        if not isinstance(sku, str) or not sku:
            LOG.warning("QuickBooks inventory item %s has no SKU; skipped", item_id)
            missing += 1
            continue
        if sku in seen_skus:
            raise SyncError(f"Multiple QuickBooks items map to Magento SKU {sku!r}; no writes performed")
        seen_skus.add(sku)
        qty = number(item.get("QtyOnHand"), f"quantity for item {item_id}")
        candidates.append((item_id, sku, qty))
    return candidates, missing


def sync(qbo, magento, report, apply=False, skus=None):
    # Fetch and validate the entire QuickBooks snapshot before any Magento write.
    candidates, missing = plan_items(qbo.inventory())
    if skus:
        selected = set(skus)
        found = {sku for _, sku, _ in candidates}
        unknown = selected - found
        if unknown:
            raise SyncError("Requested SKU(s) not found in active QuickBooks inventory: " + ", ".join(sorted(unknown)))
        candidates = [item for item in candidates if item[1] in selected]
    writer = csv.DictWriter(report, fieldnames=["item_id", "sku", "old_qty", "new_qty", "old_in_stock", "new_in_stock", "result"])
    writer.writeheader()
    errors, changed = missing, 0
    for item_id, sku, qty in candidates:
        row = {"item_id": item_id, "sku": sku, "new_qty": qty}
        try:
            stock = magento.stock(sku)
            if stock is None:
                row["result"] = "skipped_product_type"
            elif not flag(stock["manage_stock"]):
                row["result"] = "skipped_unmanaged"
            else:
                previous = number(stock["qty"], f"Magento quantity for {sku}")
                previous_in_stock = flag(stock["is_in_stock"])
                minimum = number(stock["min_qty"], f"Magento threshold for {sku}")
                backorders = number(stock["backorders"], "backorders setting")
                if backorders not in (0, 1, 2):
                    raise SyncError("Unexpected Magento backorders setting")
                in_stock = backorders != 0 or qty > minimum
                row.update(old_qty=previous, old_in_stock=previous_in_stock, new_in_stock=in_stock)
                if previous == qty and previous_in_stock == in_stock:
                    row["result"] = "unchanged"
                elif apply:
                    magento.update(sku, stock["item_id"], qty, in_stock)
                    row["result"] = "updated"
                    changed += 1
                else:
                    row["result"] = "would_update"
                    changed += 1
        except (SyncError, KeyError, ValueError, TypeError) as exc:
            errors += 1
            row["result"] = "error"
            LOG.error("SKU %r failed (%s)", sku, type(exc).__name__)
            if isinstance(exc, ApiError):
                LOG.error("HTTP %s", exc.status)
                if exc.status in (401, 403):
                    writer.writerow(row)
                    report.flush()
                    raise SyncError("Magento authentication/permission failure; stopping") from None
        writer.writerow(row)
        report.flush()
        if row["result"] not in ("unchanged", "skipped_product_type", "skipped_unmanaged"):
            LOG.info("SKU %r: %s (%s -> %s)", sku, row["result"], row.get("old_qty", "?"), qty)
    LOG.info("%s: %d candidates, %d changes, %d errors/unmapped items",
             "APPLY" if apply else "DRY RUN", len(candidates), changed, errors)
    return 1 if errors else 0


def flag(value):
    if isinstance(value, bool) or (isinstance(value, int) and value in (0, 1)):
        return bool(value)
    raise SyncError("Invalid Magento boolean stock setting")


def run_stock_sync(apply=False, skus=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        # Check Magento configuration before a QBO token refresh can occur.
        http = Http()
        magento = Magento(STOCK_CONFIG, http)
        state = Path(STOCK_CONFIG["report_dir"])
        if not state.is_absolute():
            state = APP_DIR / state
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = os.open(state / "stock-sync.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SyncError("Another stock-sync process is running") from None
            qbo = QuickBooks(QBO_CONFIG, http)
            filename = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}.csv"
            path = state / filename
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            LOG.info("%s; report: %s", "APPLY" if apply else "DRY RUN (no Magento writes)", path)
            with os.fdopen(fd, "w", newline="") as report:
                return sync(qbo, magento, report, apply=apply, skus=skus)
    except SyncError as error:
        LOG.error("Stock sync stopped: %s", error)
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Never log arbitrary third-party exceptions containing request credentials.
        LOG.error("Stock sync stopped (%s); check configuration and API access", type(error).__name__)
    return 1


