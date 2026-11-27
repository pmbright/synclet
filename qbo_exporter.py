import json
import requests
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from qbo_token_store import get_access_token, get_realm_id


class QBOExporter:
    def __init__(self, connection, config, debug=False):
        self.connection = connection
        self.config = config
        self.debug = debug

    # ========================
    # HEADERS
    # ========================
    def _headers(self):
        return {
            "Authorization": f"Bearer {get_access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "minorversion": "62"
        }


    # ========================
    # HELPERS (UNCHANGED FROM POC)
    # ========================
    def _qbo_query(self, query):
        url = f"{self.config['qbo']['base_url']}/v3/company/{get_realm_id()}/query"
        r = requests.get(url, headers=self._headers(), params={"query": query})
        r.raise_for_status()
        return r.json()["QueryResponse"]

    def _get_item_id_by_name(self, name):
        # Hack to fix product sku issue
        if (name == "BBUV1"):
            name = "BBUV1-Large"
        if (name == "BBU7TV-BBTVS"):
            name = "BBU7TV"
        if (name == "BB7TV-Rose-BBTVS"):
            name = "BBU7TV-Rose"

        if self.debug:
            print(f"Looking up QBO item by NAME (SKU): {name}")
        resp = self._qbo_query(f"SELECT Id FROM Item WHERE Name = '{name}'")
        items = resp.get("Item", [])
        if not items:
            raise Exception(f"QBO item not found for Name (SKU): {name}")
        return items[0]["Id"]

    def _get_tax_code_id_by_name(self, name):
        if self.debug:
            print(f"Looking up TaxCode: {name}")
        resp = self._qbo_query(f"SELECT Id FROM TaxCode WHERE Name = '{name}'")
        codes = resp.get("TaxCode", [])
        if not codes:
            raise Exception(f"TaxCode not found: {name}")
        return codes[0]["Id"]

    def _get_invoice(self, invoice_id):
        url = f"{self.config['qbo']['base_url']}/v3/company/{get_realm_id()}/invoice/{invoice_id}"
        r = requests.get(url, headers=self._headers())
        r.raise_for_status()
        return r.json()["Invoice"]

    def _money(self, val):
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ========================
    # EXPORT ENTRY (UNCHANGED BEHAVIOUR)
    # ========================
    def export_orders(self, order_ids=None, limit=None, export_complete_only=True):
        cursor = self.connection.cursor(dictionary=True)

        query = """
            SELECT o.order_number, o.raw_json, o.order_date
            FROM orders o
            LEFT JOIN csv_export_history c ON c.order_number = o.order_number
            LEFT JOIN qbo_export_history q ON q.order_number = o.order_number
            WHERE c.order_number IS NULL
            AND q.order_number IS NULL
        """

        params = []

        if export_complete_only:
            query += " AND o.status IN ('complete', 'processing')"

        if order_ids:
            placeholders = ",".join(["%s"] * len(order_ids))
            query += f" AND o.order_number IN ({placeholders})"
            params.extend(order_ids)

        query += " ORDER BY order_date DESC"

        if limit:
            query += " LIMIT %s"
            params.append(limit)

        cursor.execute(query, params)
        orders = cursor.fetchall()

        print(f"Found {len(orders)} orders to export")

        vat_0 = self._get_tax_code_id_by_name(self.config["qbo"]["vat_0_name"])
        vat_20 = self._get_tax_code_id_by_name(self.config["qbo"]["vat_20_name"])

        shipping_item_id = self._get_item_id_by_name(self.config["qbo"]["shipping_item_name"])
        adjustment_item_id = self._get_item_id_by_name(self.config["qbo"]["adjustment_item_name"])

        for order in orders:
            self._export_order(order, vat_0, vat_20, shipping_item_id, adjustment_item_id)

    # ========================
    # CORE LOGIC (RAW JSON ONLY)
    # ========================
    def _export_order(self, order, vat_0, vat_20, shipping_item_id, adjustment_item_id):
        print(f"\nExporting Order: {order['order_number']}")

        raw = json.loads(order["raw_json"])

        items = raw["Items"]
        shipping = raw.get("Shipping", {})
        contact = raw.get("Contact", {})
        addresses = raw.get("Addresses", {})

        lines = []
        payload_total = Decimal("0.00")

        for item in items:
            qty = Decimal(item["Quantity"])
            unit_ex = Decimal(item.get("UnitPriceExTax", "0"))
            discount = Decimal(item.get("Discount", 0) or 0)

            tax_rate = Decimal(item.get("Taxes", {}).get("TaxRate", 0) or 0)

            if discount > 0 and tax_rate > 0:
                discount_ex = discount / (1 + tax_rate)
            elif discount > 0:
                discount_ex = discount
            else:
                discount_ex = Decimal("0")

            net_unit = unit_ex - discount_ex

            amount = self._money(net_unit * qty)
            payload_total += amount

            country = raw.get("Addresses", {}).get("ShippingAddress", {}).get("CountryCode")
            is_uk = country == "GB"

            tax_code = vat_20 if tax_rate > 0 else vat_0

            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": float(amount),
                "Description": item["ProductName"],
                "SalesItemLineDetail": {
                    "ItemRef": {"value": self._get_item_id_by_name(item["ProductCode"])},
                    "Qty": int(qty),
                    "UnitPrice": float(self._money(net_unit)),
                    "TaxCodeRef": {"value": tax_code}
                }
            })

        # Shipping (EXACTLY like PoC)
        shipping_inc = Decimal(shipping.get("Amount", "0") or 0)
        shipping_tax = Decimal(shipping.get("Taxes", {}).get("TaxAmount", 0) or 0)
        shipping_tax_rate = Decimal(shipping.get("Taxes", {}).get("TaxRate", 0) or 0)

        if shipping_tax_rate > 0:
            shipping_ex = shipping_inc - shipping_tax
        else:
            shipping_ex = shipping_inc

        tax_code = vat_20 if shipping_tax_rate > 0 else vat_0

        lines.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": float(self._money(shipping_ex)),
            "Description": "Shipping",
            "SalesItemLineDetail": {
                "ItemRef": {"value": shipping_item_id},
                "Qty": 1,
                "UnitPrice": float(self._money(shipping_ex)),
                "TaxCodeRef": {"value": tax_code}
            }
        })

        merged_lines = {}

        for line in lines:
            key = (
                line["SalesItemLineDetail"]["ItemRef"]["value"],
                line["SalesItemLineDetail"]["TaxCodeRef"]["value"],
                line["SalesItemLineDetail"]["UnitPrice"],
            )

            if key not in merged_lines:
                merged_lines[key] = line
            else:
                merged = merged_lines[key]
                merged["SalesItemLineDetail"]["Qty"] += line["SalesItemLineDetail"]["Qty"]
                merged["Amount"] += line["Amount"]

        lines = list(merged_lines.values())


        payload_total += shipping_ex

        # ========================
        # BUILD PAYLOAD (MATCH POC EXACTLY)
        # ========================
        payload = {
            "CustomerRef": {"value": str(self.config["qbo"]["customer_id"])},
            #"TxnDate": datetime.now().strftime("%Y-%m-%d"),
            "TxnDate": order["order_date"].strftime("%Y-%m-%d"),
            "DocNumber": f"M{order['order_number']}",
            "GlobalTaxCalculation": "TaxExcluded",
            "BillEmail": {"Address": contact.get("Email", "")},
            "BillAddr": {
                "Line1": addresses.get("BillingAddress", {}).get("Line1"),
                "Line2": addresses.get("BillingAddress", {}).get("Line2"),
                "City": addresses.get("BillingAddress", {}).get("City"),
                "CountrySubDivisionCode": addresses.get("BillingAddress", {}).get("State"),
                "PostalCode": addresses.get("BillingAddress", {}).get("PostCode"),
                "Country": addresses.get("BillingAddress", {}).get("CountryCode"),
            },
            "ShipAddr": {
                "Line1": addresses.get("ShippingAddress", {}).get("Line1"),
                "Line2": addresses.get("ShippingAddress", {}).get("Line2"),
                "City": addresses.get("ShippingAddress", {}).get("City"),
                "CountrySubDivisionCode": addresses.get("ShippingAddress", {}).get("State"),
                "PostalCode": addresses.get("ShippingAddress", {}).get("PostCode"),
                "Country": addresses.get("ShippingAddress", {}).get("CountryCode"),
            },
            "Line": lines,
            "PrivateNote": f"Magento Order #{order['order_number']}\nDiscount applied: £{raw.get('Discounts', 0)}"
        }

        if self.debug:
            print("\n--- DEBUG INVOICE ---")
            print(json.dumps(payload, indent=2))

        # ========================
        # CREATE
        # ========================
        r = requests.post(
            f"{self.config['qbo']['base_url']}/v3/company/{get_realm_id()}/invoice",
            headers=self._headers(),
            data=json.dumps(payload)
        )

        if not r.ok:
            print("[QBO CREATE ERROR BODY]")
            print(r.text)

        r.raise_for_status()

        invoice = r.json()["Invoice"]

        invoice_id = invoice["Id"]
        sync_token = invoice["SyncToken"]

        if self.debug:
            print(f"\n[QBO] Created invoice {invoice_id} for order {order['order_number']}")

        # ========================
        # RECONCILE (EXACT POC + DEBUG)
        # ========================
        # ========================
        # RECONCILE (WITH FALLBACK LINE FIX)
        # ========================
        invoice_full = self._get_invoice(invoice_id)

        qbo_total = self._money(invoice_full["TotalAmt"])
        expected = self._money(raw["Total"])
        diff = self._money(expected - qbo_total)

        adjustment_applied = False
        adjustment_ignored = False

        if abs(diff) >= Decimal("0.01"):

            if self.debug:
                print(f"[QBO] Applying adjustment {diff}")

            try:
                # --- FIRST TRY: STANDARD ADJUSTMENT LINE ---
                invoice_full["Line"].append({
                    "DetailType": "SalesItemLineDetail",
                    "Amount": float(diff),
                    "Description": "Rounding Adjustment",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": adjustment_item_id},
                        "Qty": 1,
                        "UnitPrice": float(diff),
                        "TaxCodeRef": {"value": vat_0}
                    }
                })

                r = requests.post(
                    f"{self.config['qbo']['base_url']}/v3/company/{get_realm_id()}/invoice",
                    headers=self._headers(),
                    data=json.dumps(invoice_full)
                )
                r.raise_for_status()

                invoice_full = r.json()["Invoice"]
                adjustment_applied = True

            except requests.exceptions.HTTPError as e:
                print("[WARN] Adjustment line failed, attempting targeted fix...")

                if e.response is not None:
                    print("[QBO ERROR BODY]")
                    print(e.response.text)

                # ========================
                # FALLBACK: LINE-LEVEL FIX
                # ========================
                invoice_full = self._get_invoice(invoice_id)

                # Build Magento gross map
                magento_lines = []

                for item in raw["Items"]:
                    gross = Decimal(item.get("LineTotalIncTax", 0) or 0)
                    magento_lines.append({
                        "desc": item["ProductName"],
                        "gross": self._money(gross)
                    })

                shipping_inc = Decimal(raw.get("Shipping", {}).get("Amount", 0) or 0)
                if shipping_inc > 0:
                    magento_lines.append({
                        "desc": "Shipping",
                        "gross": self._money(shipping_inc)
                    })

                # Find worst matching line
                best_line = None
                best_diff = Decimal("0.00")

                for line in invoice_full["Line"]:
                    if line.get("DetailType") != "SalesItemLineDetail":
                        continue

                    desc = line.get("Description")
                    net = Decimal(str(line["Amount"]))

                    tax_code = line["SalesItemLineDetail"]["TaxCodeRef"]["value"]

                    if tax_code == vat_20:
                        qbo_gross = self._money(net * Decimal("1.2"))
                    else:
                        qbo_gross = net

                    for m in magento_lines:
                        if m["desc"] == desc:
                            d = m["gross"] - qbo_gross
                            if abs(d) > abs(best_diff):
                                best_diff = d
                                best_line = line

                if best_line:
                    if self.debug:
                        print(f"[FIX] Adjusting line: {best_line.get('Description')} by {diff}")

                    qty = Decimal(best_line["SalesItemLineDetail"]["Qty"])
                    current_amount = Decimal(str(best_line["Amount"]))

                    new_amount = self._money(current_amount + diff)
                    new_unit_price = self._money(new_amount / qty)

                    best_line["Amount"] = float(new_amount)
                    best_line["SalesItemLineDetail"]["UnitPrice"] = float(new_unit_price)

                    try:
                        r = requests.post(
                            f"{self.config['qbo']['base_url']}/v3/company/{get_realm_id()}/invoice",
                            headers=self._headers(),
                            data=json.dumps(invoice_full)
                        )
                        r.raise_for_status()

                        invoice_full = r.json()["Invoice"]
                        adjustment_applied = True

                    except requests.exceptions.HTTPError as e2:
                        print(f"[WARN] Targeted fix failed: {e2}")

                        if e2.response is not None:
                            print("[QBO ERROR BODY]")
                            print(e2.response.text)


                        # FINAL fallback → ignore small rounding issues
                        if abs(diff) <= Decimal("0.02"):
                            print(f"[SKIP] Ignoring rounding diff {diff}")
                            adjustment_ignored = True
                            diff = Decimal("0.00")
                        else:
                            raise

                else:
                    print("[ERROR] No suitable line found to adjust")

                    # FINAL fallback → ignore small rounding issues
                    if abs(diff) <= Decimal("0.02"):
                        print(f"[SKIP] Ignoring rounding diff {diff}")
                        adjustment_ignored = True
                        diff = Decimal("0.00")
                    else:
                        raise

        # LOG TO DB
        cursor = self.connection.cursor()

        cursor.execute("""
            INSERT INTO qbo_export_history (
                order_number,
                qbo_invoice_id,
                adjustment_applied,
                adjustment_value,
                status
            )
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                qbo_invoice_id = VALUES(qbo_invoice_id),
                adjustment_applied = VALUES(adjustment_applied),
                adjustment_value = VALUES(adjustment_value),
                status = 'success',
                error = NULL
        """, (
            order["order_number"],
            invoice_id,
            adjustment_applied,
            float(diff) if adjustment_applied else 0,
            "success"
        ))

        self.connection.commit()

        print(f"[TRACK] Logged QBO export for order {order['order_number']}")


        # ========================
        # FINAL DEBUG OUTPUT
        # ========================
        print("\n--- RESULT ---")
        print(f"Order: {order['order_number']}")
        print(f"QBO Invoice ID: {invoice_id}")
        print(f"QBO total: {qbo_total}")
        print(f"Expected: {expected}")
        print(f"Diff: {diff:+}")
        print(f"Adjustment applied: {'YES' if adjustment_applied else 'NO'}")

        if adjustment_applied:
            final_total = invoice_full["TotalAmt"]
            print(f"Final Total: {final_total}")

        print("---------------")

