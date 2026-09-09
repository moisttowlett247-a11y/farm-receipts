import os
import re

DATA_FILE = "Receipt_Data.txt"

KNOWN_HEADERS = {
    "file name:", "category:", "vendor:", "receipt date:", 
    "date source line:", "payment method:", "reference #:", 
    "subtotal:", "sales tax:", "amount:", "items:"
}

def consolidate_items_by_barcode(block_text):
    """
    Scans line items under 'Items:' for UPC/barcode digits (6-14 digits).
    Consolidates identical barcode entries, sums their prices, and tags quantities.
    """
    lines = block_text.splitlines()
    output_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.strip().lower().startswith("items:"):
            output_lines.append(line)
            i += 1
            
            seen_barcodes = {}
            non_barcode_items = []
            
            while i < len(lines):
                sub_line = lines[i]
                stripped_sub = sub_line.strip()
                
                # Exit item loop on block boundary or next header
                if stripped_sub.startswith("----------------") or stripped_sub.startswith("============="):
                    break
                
                key_prefix = stripped_sub.split(":", 1)[0].lower() + ":" if ":" in stripped_sub else ""
                if key_prefix in KNOWN_HEADERS:
                    break

                if sub_line.startswith("  - ") or sub_line.startswith("- "):
                    # Extract barcode sequence (6 to 14 digits)
                    barcode_match = re.search(r"\b(\d{6,14})\b", sub_line)
                    
                    if barcode_match:
                        barcode = barcode_match.group(1)
                        
                        # Extract price ($XX.XX)
                        price_match = re.search(r"\$\s*(\d+(?:\.\d+)?)", sub_line)
                        price = float(price_match.group(1)) if price_match else 0.0
                        
                        # Extract existing quantity if present
                        qty_match = re.search(r"\(Qty:\s*(\d+)\)", sub_line, re.IGNORECASE)
                        qty = int(qty_match.group(1)) if qty_match else 1
                        
                        # Clean item base string (strip price and quantity tags)
                        clean_base = re.sub(r"\s*-\s*\$\d+(?:\.\d+)?", "", sub_line)
                        clean_base = re.sub(r"\$\d+(?:\.\d+)?", "", clean_base)
                        clean_base = re.sub(r"\(Qty:\s*\d+\)", "", clean_base, flags=re.IGNORECASE).rstrip()

                        if barcode in seen_barcodes:
                            seen_barcodes[barcode]['total_price'] += price
                            seen_barcodes[barcode]['qty'] += qty
                        else:
                            seen_barcodes[barcode] = {
                                'base_text': clean_base,
                                'total_price': price,
                                'qty': qty
                            }
                    else:
                        non_barcode_items.append(sub_line)
                else:
                    non_barcode_items.append(sub_line)
                
                i += 1

            # Write consolidated barcode items with summed total and (Qty: X)
            for item in seen_barcodes.values():
                base_text = item['base_text']
                tot_p = item['total_price']
                q_val = item['qty']
                
                price_str = f" - ${tot_p:.2f}" if tot_p > 0 else ""
                qty_str = f" (Qty: {q_val})" if q_val > 1 else ""
                output_lines.append(f"{base_text}{price_str}{qty_str}")

            # Write non-barcode items
            for item_line in non_barcode_items:
                output_lines.append(item_line)

        else:
            output_lines.append(line)
            i += 1

    return "\n".join(output_lines)


def sort_and_deduplicate_receipt_file():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    raw_blocks = re.split(r"-{50,}", content)

    receipt_blocks = []
    seen_fingerprints = set()

    for block in raw_blocks:
        block_str = block.strip()
        if not block_str or "BATCH RUN DATE" in block_str:
            continue

        # Step 1: Consolidate barcode items, sum totals, and update quantities
        consolidated_block = consolidate_items_by_barcode(block_str)

        # Step 2: Unique Fingerprint for whole-receipt deduplication
        file_m = re.search(r"File Name:\s*(.*)", consolidated_block)
        ref_m = re.search(r"Reference #:\s*(.*)", consolidated_block)
        vendor_m = re.search(r"Vendor:\s*(.*)", consolidated_block)
        date_m = re.search(r"Receipt Date:\s*(.*)", consolidated_block)
        amount_m = re.search(r"Amount:\s*(.*)", consolidated_block)

        file_id = file_m.group(1).strip().lower() if file_m else ""
        ref_id = ref_m.group(1).strip().lower() if ref_m else ""
        vendor_id = vendor_m.group(1).strip().lower() if vendor_m else ""
        date_id = date_m.group(1).strip().lower() if date_id else ""
        amount_id = amount_m.group(1).strip().lower() if amount_m else ""

        if vendor_id and date_id and amount_id:
            fingerprint = f"{file_id}|{ref_id}|{vendor_id}|{date_id}|{amount_id}"
        else:
            fingerprint = re.sub(r"\s+", "", consolidated_block).lower()

        if fingerprint in seen_fingerprints:
            continue

        seen_fingerprints.add(fingerprint)
        receipt_blocks.append(consolidated_block)

    def get_sorting_date(block_text):
        match = re.search(r"Receipt Date:\s*([\d:\s\w\[\]\-]+)", block_text)
        if match:
            date_str = match.group(1).strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
                return date_str
        return "9999-99-99"

    receipt_blocks.sort(key=get_sorting_date)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        for block in receipt_blocks:
            f.write(block + "\n--------------------------------------------------\n")

    print(f"Successfully processed {len(receipt_blocks)} receipt blocks.")


if __name__ == "__main__":
    sort_and_deduplicate_receipt_file()
