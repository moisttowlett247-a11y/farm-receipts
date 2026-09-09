import os
import re

DATA_FILE = "Receipt_Data.txt"

def consolidate_block_items(block_text):
    """Parses items inside a single receipt block, combines duplicates, and sums prices/quantities."""
    lines = block_text.splitlines()
    new_lines = []
    in_items_section = False
    items_dict = {}

    for line in lines:
        # Detect the start of the Items list
        if line.strip().startswith("Items:"):
            in_items_section = True
            new_lines.append(line)
            continue
        
        # Stop item processing if a separator or new section header is reached
        if in_items_section and (line.startswith("----------------") or line.startswith("=============") or not line.startswith("  - ")):
            # Flush consolidated items into output lines
            for item_info in items_dict.values():
                name = item_info['name']
                qty = item_info['qty']
                tot_price = item_info['total_price']
                
                price_str = f" - ${tot_price:.2f}" if tot_price > 0 else ""
                qty_str = f" (Qty: {qty})" if qty > 1 else ""
                new_lines.append(f"  - {name}{price_str}{qty_str}")
            
            in_items_section = False
            items_dict = {}
            new_lines.append(line)
            continue

        if in_items_section:
            # Match standard format: "  - ITEM NAME - $PRICE (Qty: X)" or "  - ITEM NAME - $PRICE"
            match = re.match(r"^\s*-\s*(.*?)(?:\s*-\s*\$(\d+(?:\.\d+)?))?(?:\s*\(Qty:\s*(\d+)\))?$", line)
            if match:
                raw_name, price_str, qty_str = match.groups()
                raw_name = raw_name.strip()
                clean_key = re.sub(r'\s+', ' ', raw_name).upper()
                
                price = float(price_str) if price_str else 0.0
                qty = int(qty_str) if qty_str else 1

                if clean_key in items_dict:
                    items_dict[clean_key]['qty'] += qty
                    items_dict[clean_key]['total_price'] += price
                else:
                    items_dict[clean_key] = {
                        'name': raw_name,
                        'qty': qty,
                        'total_price': price
                    }
            else:
                # If a line doesn't match standard item formatting, retain as-is
                new_lines.append(line)
        else:
            new_lines.append(line)

    # Flush any remaining items if block ended inside the items section
    if in_items_section and items_dict:
        for item_info in items_dict.values():
            name = item_info['name']
            qty = item_info['qty']
            tot_price = item_info['total_price']
            
            price_str = f" - ${tot_price:.2f}" if tot_price > 0 else ""
            qty_str = f" (Qty: {qty})" if qty > 1 else ""
            new_lines.append(f"  - {name}{price_str}{qty_str}")

    return "\n".join(new_lines)


def sort_and_deduplicate_receipt_file():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Split content by standard 50-dash block boundary
    raw_blocks = re.split(r"-{50,}", content)

    receipt_blocks = []
    seen_fingerprints = set()

    for block in raw_blocks:
        block = block.strip()
        if not block or "BATCH RUN DATE" in block:
            continue

        # Step 1: Consolidate items & quantities inside this block
        consolidated_block = consolidate_block_items(block)

        # Step 2: Create fingerprint to deduplicate identical receipt blocks
        fingerprint = re.sub(r"\s+", "", consolidated_block)
        if fingerprint in seen_fingerprints:
            continue

        seen_fingerprints.add(fingerprint)
        receipt_blocks.append(consolidated_block)

    # Helper function to extract date for sorting
    def get_sorting_date(block_text):
        match = re.search(r"Receipt Date:\s*([\d:\s\w\[\]\-]+)", block_text)
        if match:
            date_str = match.group(1).strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
                return date_str
        return "9999-99-99"

    # Sort blocks chronologically
    receipt_blocks.sort(key=get_sorting_date)

    # Rewrite cleaned and consolidated output back to Receipt_Data.txt
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        for block in receipt_blocks:
            f.write(block + "\n--------------------------------------------------\n")

    print(f"Successfully processed {len(receipt_blocks)} receipt blocks with consolidated quantities.")


if __name__ == "__main__":
    sort_and_deduplicate_receipt_file()
