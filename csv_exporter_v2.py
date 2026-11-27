import os
import csv
import re
import json
from datetime import datetime
from config import CSV_EXPORT_DIR, CSV_EXPORT_SIMPLE, CSV_EXPORT_ON_SYNC


class CSVExporter:
    def __init__(self, db_connection):
        self.connection = db_connection

    def _gross_to_net_discount(self, gross_discount, tax_rate):
        """
        Convert a gross discount to net for QBO when Global Tax Calculation = TaxInclusive
        """
        if tax_rate > 0:
            return round(gross_discount / (1 + tax_rate), 2)
        return round(gross_discount, 2)

    def _qbo_tax_code_and_rate(self, taxes: dict):
        """
        Determine QBO tax code and rate from Magento tax data.
        """
        if not isinstance(taxes, dict):
            return "0.0% Z", 0.0

        try:
            rate = float(taxes.get("TaxRate") or 0)
        except (TypeError, ValueError):
            rate = 0.0

        if rate > 0:
            return "20.0% S", rate

        return "0.0% Z", 0.0

    def export_orders(self, order_ids=None, start_date=None, end_date=None, simple_format=None, export_complete_only=True, track_exports=True):
        """Export orders to CSV files
        
        Args:
            order_ids: List of specific order IDs to export
            start_date: Start date for date range filter
            end_date: End date for date range filter  
            simple_format: Use simple format (line items only)
            export_complete_only: Only export orders with status='complete'
            track_exports: Track which orders have been exported to avoid duplicates
        """
        cursor = self.connection.cursor(dictionary=True)
        
        # Build query
        query = """
            SELECT o.* FROM orders o
            WHERE 1=1
        """
        
        params = []
        
        # Add filters
        if export_complete_only:
            query += " AND o.status = 'complete'"
        else:
            # Include complete, processing, and closed orders
            query += " AND o.status IN ('complete', 'processing', 'closed')"
            
        if order_ids:
            placeholders = ','.join(['%s'] * len(order_ids))
            query += f" AND o.id IN ({placeholders})"
            params.extend(order_ids)
        
        if start_date:
            query += " AND o.order_date >= %s"
            params.append(start_date)
            
        if end_date:
            query += " AND o.order_date <= %s"
            params.append(end_date)
            
        if track_exports:
            # Skip orders that were already exported
            query += """ 
                AND NOT EXISTS (
                    SELECT 1 FROM csv_export_history ce 
                    WHERE ce.order_id = o.id 
                    AND ce.export_type = %s
                )
            """
            export_type = 'simple' if (simple_format if simple_format is not None else CSV_EXPORT_SIMPLE) else 'full'
            params.append(export_type)
        
        query += " ORDER BY o.order_date DESC"
        
        cursor.execute(query, params)
        orders = cursor.fetchall()
        
        if not orders:
            print("No orders to export")
            return
            
        print(f"Found {len(orders)} orders to export")
        
        # Use config value if simple_format not specified
        use_simple = simple_format if simple_format is not None else CSV_EXPORT_SIMPLE
        
        # Create timestamp for filename
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base_export_dir = CSV_EXPORT_DIR
        
        # Create directory if it doesn't exist
        if not os.path.exists(base_export_dir):
            os.makedirs(base_export_dir)
        
        # Create single CSV filename
        csv_filename = f"invoices-{timestamp}.csv"
        csv_filepath = os.path.join(base_export_dir, csv_filename)
        
        # Create order numbers list filename
        txt_filename = f"invoices-{timestamp}.txt"
        txt_filepath = os.path.join(base_export_dir, txt_filename)
        
        print(f"Exporting to file: {csv_filepath}")
        print(f"Export format: {'Simple (SaaSant)' if use_simple else 'Full invoice'}")
        
        # Export all orders to a single CSV file
        exported_count = 0
        order_numbers = []
        
        try:
            if use_simple:
                exported_count, order_numbers = self._export_all_orders_simple(orders, csv_filepath)
            else:
                exported_count, order_numbers = self._export_all_orders_full(orders, csv_filepath)
            
            # Write order numbers to text file
            with open(txt_filepath, 'w') as txt_file:
                txt_file.write(f"Orders exported on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                txt_file.write(f"Total orders: {len(order_numbers)}\n")
                txt_file.write("-" * 40 + "\n")
                for order_num in order_numbers:
                    txt_file.write(f"{order_num}\n")
            
            print(f"Created order list file: {txt_filepath}")
            
            # Record exports if tracking is enabled
            if track_exports:
                for order in orders[:exported_count]:
                    self._record_csv_export(order['id'], order['order_number'], csv_filepath, 'simple' if use_simple else 'full')
                    
        except Exception as e:
            print(f"Error during export: {e}")
            
        print(f"Successfully exported {exported_count} orders to {csv_filename}")
        
    def _record_csv_export(self, order_id, order_number, csv_file, export_type):
        """Record that an order was exported to CSV"""
        try:
            cursor = self.connection.cursor()
            query = """
                INSERT INTO csv_export_history (order_id, order_number, export_time, file_path, export_type)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(query, (order_id, order_number, datetime.now(), csv_file, export_type))
            self.connection.commit()
        except Exception as e:
            print(f"Error recording CSV export: {e}")
            self.connection.rollback()
    
    def _export_all_orders_simple(self, orders, csv_filepath):
        """Export all orders to a single CSV file in SaaSant format"""
        exported_count = 0
        order_numbers = []
        
        with open(csv_filepath, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            
            # Write header row only once
            writer.writerow([
                "Invoice No", "Customer", "Invoice Date", "Product/Service", 
                "Product/Service Description", "Product/Service Quantity", "Product/Service Amount", 
                "Product/Service Tax Code", "Product/Service Tax Amount", "Discount Amount",
                "Discount Account", "Apply Tax After Discount", "Payment method",
                "Payment Date", "Payment Amount", "Payment Account", "Global Tax Calculation",
                "Email", "Ship Method", "Ship Via", "Shipping Address Line 1",
                "Shipping Address Line 2", "Shipping Address Line 3", "Shipping Address Country",
                "Shipping Address City", "Shipping Address Postal Code", 
                "Shipping Address Country Subdivision Code", "Shipping Amount", "Shipping Tax Code",
                "Billing Address Line 1", "Billing Address Line 2", "Billing Address Line 3",
                "Billing Address City", "Billing Address Country", "Billing Address Postal Code",
                "Billing Address Country Subdivision Code", "Memo"
            ])
            
            # Process each order
            for order in orders:
                try:
                    # Export order rows to the same file
                    self._write_order_simple_rows(writer, order)
                    exported_count += 1
                    order_numbers.append(order['order_number'])
                    print(f"Added order {order['order_number']} to CSV")
                except Exception as e:
                    print(f"Error exporting order {order['order_number']}: {e}")
                    
        return exported_count, order_numbers
    
    def _export_all_orders_full(self, orders, csv_filepath):
        """Export all orders to a single CSV file in full format"""
        # For now, just use simple format
        return self._export_all_orders_simple(orders, csv_filepath)
    
    def _write_order_simple_rows(self, writer, order):
        """Write order data rows to CSV writer in SaaSant format"""
        magento_order = order['order_number']
        
        # Parse the JSON from raw_json field
        try:
            raw_data = json.loads(order.get('raw_json', '{}'))
        except json.JSONDecodeError:
            print(f"Error parsing JSON for order {magento_order}")
            raw_data = {}
        
        # Extract data from JSON structure
        contact = raw_data.get('Contact', {})
        addresses = raw_data.get('Addresses', {})
        billing_addr = addresses.get('BillingAddress', {})
        shipping_addr = addresses.get('ShippingAddress', billing_addr)  # default to billing if no shipping
        shipping_info = raw_data.get('Shipping', {})
        payments = raw_data.get('Payments', {})
        payment_method = payments.get('PaymentMethod', {})
        items = raw_data.get('Items', [])
        
        # Common data for all rows
        invoice_no = f"M{magento_order}"
        customer = "Magento - BBwear"
        order_date = order['order_date'].strftime('%d/%m/%Y') if order['order_date'] else ''
        contact_email = contact.get('Email', '')
        
        # Payment info
        payment_amount = str(payment_method.get('Amount', raw_data.get('Total', '0.00')))
        
        # Shipping info
        shipping_method = shipping_info.get('ShippingMethod', '')
        shipping_amount = float(shipping_info.get('Amount', 0))
        # IMPORTANT: We let QuickBooks calculate tax. Do NOT export tax amounts.
        # Use the tax *code* (if present) to decide whether shipping is taxable.
        shipping_taxes = shipping_info.get('Taxes', {}) or {}
        shipping_tax_code, _ = self._qbo_tax_code_and_rate(shipping_taxes)
            
        # Helper function
        def format_address_line1(addr):
            """Format name with optional salutation"""
            parts = []
            if addr.get('Salutation') and addr['Salutation'].strip():
                parts.append(addr['Salutation'])
            if addr.get('FirstName'):
                parts.append(addr['FirstName'])
            if addr.get('LastName'):
                parts.append(addr['LastName'])
            return ' '.join(parts)
        
        # Process items
        first_item = True
        for item in items:
            # Get item details
            product_code = item.get('ProductCode', '')
            product_name = item.get('ProductName', '')
            quantity = str(int(float(item.get('Quantity', 0))))
            line_total = str(item.get('LineTotalIncTax', '0.00'))
            
            # Get tax info
            taxes = item.get('Taxes', {}) or {}
            tax_code, tax_rate = self._qbo_tax_code_and_rate(taxes)
            tax_amount = ''
            
            # Get item discount if any
            # OneSaas exposes item Discount as a *per-unit* amount.
            # SaaSant expects a line-level discount amount.
            item_discount_unit = float(item.get('Discount', 0) or 0)
            qty_num = float(item.get('Quantity', 0) or 0)
            gross_discount = item_discount_unit * qty_num

            item_discount = self._gross_to_net_discount(gross_discount, tax_rate)
            
            # Build row
            row = []
            
            # Fields 1-3: Invoice info (always populated)
            row.extend([invoice_no, customer, order_date])
                
            # Fields 4-9: Item info (always populated)
            row.extend([
                product_code,
                product_name,
                quantity,
                line_total,
                tax_code,
                tax_amount
            ])
            
            # Fields 10-12: Discount fields
            row.extend([
                str(item_discount) if item_discount > 0 else '',
                "Discounts given" if item_discount > 0 else '',
                "true" if first_item else ''  # Apply Tax After Discount
            ])
            
            # Fields 13-17: Payment fields (only for first item)
            if first_item:
                row.extend([
                    '', #"iwd_checkout_pay",  # Payment method
                    '', #order_date,  # Payment Date
                    '', #payment_amount,  # Payment Amount
                    '', #"Paypal A/c",  # Payment Account
                    "TaxInclusive"  # Global Tax Calculation
                ])
            else:
                row.extend([''] * 5)
                
            # Field 18: Email (only for first item)
            row.append(contact_email if first_item else '')
            
            # Fields 19-29: Shipping fields (only for first item)
            if first_item:
                row.extend([
                    "Shipping",  # Ship Method
                    shipping_method[:31],  # Ship Via
                    format_address_line1(shipping_addr),  # Line 1
                    shipping_addr.get('Line1', ''),  # Line 2
                    shipping_addr.get('Line2', ''),  # Line 3
                    shipping_addr.get('CountryCode', ''),  # Country
                    shipping_addr.get('City', ''),  # City
                    shipping_addr.get('PostCode', ''),  # Postal Code
                    shipping_addr.get('State', ''),  # State
                    str(shipping_amount),  # Shipping Amount
                    shipping_tax_code  # Shipping Tax Code
                ])
            else:
                row.extend([''] * 11)
                
            # Fields 30-36: Billing fields (only for first item)
            if first_item:
                row.extend([
                    format_address_line1(billing_addr),  # Line 1
                    billing_addr.get('Line1', ''),  # Line 2
                    billing_addr.get('Line2', ''),  # Line 3
                    billing_addr.get('City', ''),  # City
                    billing_addr.get('CountryCode', ''),  # Country
                    billing_addr.get('PostCode', ''),  # Postal Code
                    billing_addr.get('State', '')  # State
                ])
            else:
                row.extend([''] * 7)
                
            # Field 37: Memo (only for first item)
            row.append(invoice_no if first_item else '')
            
            writer.writerow(row)
            first_item = False

    def _export_order_simple(self, order, export_dir):
        """Export order in SaaSant format for QuickBooks import"""
        # Generate filename  
        magento_order = order['order_number']
        
        # Get surname from JSON data
        try:
            raw_data = json.loads(order.get('raw_json', '{}'))
            addresses = raw_data.get('Addresses', {})
            billing_addr = addresses.get('BillingAddress', {})
            surname = billing_addr.get('LastName', 'NoName')
        except:
            surname = 'NoName'
            
        billing_surname = self._sanitize_filename(surname)
        csv_file = os.path.join(export_dir, f"M{magento_order}-{billing_surname}.csv")
        
        # Parse the JSON from raw_json field
        try:
            raw_data = json.loads(order.get('raw_json', '{}'))
        except json.JSONDecodeError:
            print(f"Error parsing JSON for order {magento_order}")
            raw_data = {}
        
        # Extract data from JSON structure
        contact = raw_data.get('Contact', {})
        addresses = raw_data.get('Addresses', {})
        billing_addr = addresses.get('BillingAddress', {})
        shipping_addr = addresses.get('ShippingAddress', billing_addr)  # default to billing if no shipping
        shipping_info = raw_data.get('Shipping', {})
        payments = raw_data.get('Payments', {})
        payment_method = payments.get('PaymentMethod', {})
        items = raw_data.get('Items', [])
        
        # Common data for all rows
        invoice_no = f"M{magento_order}"
        customer = "Magento - BBwear"
        order_date = order['order_date'].strftime('%d/%m/%Y') if order['order_date'] else ''
        contact_email = contact.get('Email', '')
        
        # Payment info
        payment_amount = str(payment_method.get('Amount', raw_data.get('Total', '0.00')))
        
        # Shipping info
        shipping_method = shipping_info.get('ShippingMethod', '')
        shipping_amount = float(shipping_info.get('Amount', 0))
        # IMPORTANT: We let QuickBooks calculate tax. Do NOT export tax amounts.
        # Use the tax *code* (if present) to decide whether shipping is taxable.
        shipping_taxes = shipping_info.get('Taxes', {}) or {}
        shipping_tax_code, _ = self._qbo_tax_code_and_rate(shipping_taxes)
            
        # Helper functions
        def format_address_line1(addr):
            """Format name with optional salutation"""
            parts = []
            if addr.get('Salutation') and addr['Salutation'].strip():
                parts.append(addr['Salutation'])
            if addr.get('FirstName'):
                parts.append(addr['FirstName'])
            if addr.get('LastName'):
                parts.append(addr['LastName'])
            return ' '.join(parts)
        
        # Write CSV
        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            
            # Write header row
            writer.writerow([
                "Invoice No", "Customer", "Invoice Date", "Product/Service", 
                "Product/Service Description", "Product/Service Quantity", "Product/Service Amount", 
                "Product/Service Tax Code", "Product/Service Tax Amount", "Discount Amount",
                "Discount Account", "Apply Tax After Discount", "Payment method",
                "Payment Date", "Payment Amount", "Payment Account", "Global Tax Calculation",
                "Email", "Ship Method", "Ship Via", "Shipping Address Line 1",
                "Shipping Address Line 2", "Shipping Address Line 3", "Shipping Address Country",
                "Shipping Address City", "Shipping Address Postal Code", 
                "Shipping Address Country Subdivision Code", "Shipping Amount", "Shipping Tax Code",
                "Billing Address Line 1", "Billing Address Line 2", "Billing Address Line 3",
                "Billing Address City", "Billing Address Country", "Billing Address Postal Code",
                "Billing Address Country Subdivision Code", "Memo"
            ])
            
            # Process items
            first_item = True
            for item in items:
                # Get item details
                product_code = item.get('ProductCode', '')
                product_name = item.get('ProductName', '')
                quantity = str(int(float(item.get('Quantity', 0))))
                line_total = str(item.get('LineTotalIncTax', '0.00'))
                
                # Get tax info
                taxes = item.get('Taxes', {})
                tax_code = "20.0% S" if taxes.get('TaxCode') == "VAT - UK" else "0.0% Z"
                # IMPORTANT: Leave tax amount blank so QBO recalculates consistently.
                tax_amount = ''
                
                # Get item discount if any
                # Magento endpoint provides per-unit discount (BaseDiscountAmount / Qty).
                # SaaSant expects a line discount amount.
                unit_discount = float(item.get('Discount', 0) or 0)
                qty_num = float(item.get('Quantity', 0) or 0)
                gross_discount = unit_discount * qty_num

                tax_rate = 0.20 if tax_code == "20.0% S" else 0.0
                item_discount = self._gross_to_net_discount(gross_discount, tax_rate)
                
                # Build row
                row = []
                
                # Fields 1-3: Invoice info (always populated)
                row.extend([invoice_no, customer, order_date])
                    
                # Fields 4-9: Item info (always populated)
                row.extend([
                    product_code,
                    product_name,
                    quantity,
                    line_total,
                    tax_code,
                    tax_amount
                ])
                
                # Fields 10-12: Discount fields
                row.extend([
                    str(item_discount) if item_discount > 0 else '',
                    "Discounts given" if item_discount > 0 else '',
                "true" if first_item else ''  # Apply Tax After Discount
                ])
                
                # Fields 13-17: Payment fields (only for first item)
                if first_item:
                    row.extend([
                        "iwd_checkout_pay",  # Payment method
                        order_date,  # Payment Date
                        payment_amount,  # Payment Amount
                        "Paypal A/c",  # Payment Account
                        "TaxInclusive"  # Global Tax Calculation
                    ])
                else:
                    row.extend([''] * 5)
                    
                # Field 18: Email (only for first item)
                row.append(contact_email if first_item else '')
                
                # Fields 19-29: Shipping fields (only for first item)
                if first_item:
                    row.extend([
                        "Shipping",  # Ship Method
                        shipping_method[:31],  # Ship Via
                        format_address_line1(shipping_addr),  # Line 1
                        shipping_addr.get('Line1', ''),  # Line 2
                        shipping_addr.get('Line2', ''),  # Line 3
                        shipping_addr.get('CountryCode', ''),  # Country
                        shipping_addr.get('City', ''),  # City
                        shipping_addr.get('PostCode', ''),  # Postal Code
                        shipping_addr.get('State', ''),  # State
                        str(shipping_amount),  # Shipping Amount
                        shipping_tax_code  # Shipping Tax Code
                    ])
                else:
                    row.extend([''] * 11)
                    
                # Fields 30-36: Billing fields (only for first item)
                if first_item:
                    row.extend([
                        format_address_line1(billing_addr),  # Line 1
                        billing_addr.get('Line1', ''),  # Line 2
                        billing_addr.get('Line2', ''),  # Line 3
                        billing_addr.get('City', ''),  # City
                        billing_addr.get('CountryCode', ''),  # Country
                        billing_addr.get('PostCode', ''),  # Postal Code
                        billing_addr.get('State', '')  # State
                    ])
                else:
                    row.extend([''] * 7)
                    
                # Field 37: Memo (only for first item)
                row.append(invoice_no if first_item else '')
                
                writer.writerow(row)
                first_item = False
        
        return csv_file
    
    def _export_order_full(self, order, export_dir):
        """Export order in full invoice format"""
        # Implementation for full format export
        # Not implementing this since you want simple format as default
        pass
    
    def _sanitize_filename(self, text):
        """Sanitize text for use in filename"""
        # Remove or replace invalid filename characters
        text = re.sub(r'[<>:"/\\|?*]', '', str(text))
        # Replace spaces with underscores
        text = text.replace(' ', '_')
        # Limit length
        return text[:50]

