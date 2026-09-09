import os
import re

DATA_FILE = "Receipt_Data.txt"

def consolidate_block_items(block_text):
    """
    Parses items inside a single receipt block.
    Consolidates identical item names while guaranteeing zero item loss.
    """
    lines = block_text.splitlines()
    output_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Check if we hit the Items header
        if line.strip().startswith("Items:"):
            output_lines.append(line)
            i += 1
            
            items_dict = {}
            unmatched_lines = []
            
            # Read until the end of the block or next section
            while i < len(lines):
                sub_line = lines[i]
                
                # Exit item processing if we hit a metadata key, boundary, or non-item header
                if sub_line.startswith("----------------") or sub_line.startswith("============="):
                    break
                if re.match(r"^[A-Z][a-zA-Z\s]+:\s*", sub_line) and not sub_line.strip().startswith("Items:"):
                    break

                # Process potential item lines (starting with spaces/dashes or standard list formatting)
                if sub_line.strip().startswith("-") or sub_line.startswith("  "):
                    # Extract quantity if present: (Qty: X)
                    qty_match = re.search(r"\(Qty:\s*(\d+)\)", sub_line, re.IGNORECASE)
                    qty = int(qty_match.group(1)) if qty_match else 1

                    # Extract price if present: - $XX.XX or $XX.XX
                    price_match = re.search(r"\$\s*(\d+(?:\.\d+)?)", sub_line)
                    price = float(price_match.group(1)) if price_match else 0.0

                    # Clean name by stripping bullet points, prices, and Qty tags
                    clean_name = sub_line
                    clean_name = re.sub(r"^\s*-\s*", "", clean_name)  # remove leading dash/spaces
                    clean_name = re.sub(r"\s*-\s*\$\d+(?:\.\d+)?", "", clean_name)  # remove - $price
                    clean_name = re.sub(r"\$\d+(?:\.\d+)?", "", clean_name)  # remove $price
                    clean_name = re.sub(r"\(Qty:\s*\d+\)", "", clean_name, flags=re.IGNORECASE)  # remove (Qty: X)
                    clean_name = clean_name.strip()

                    if clean_name:
                        lookup_key = re.sub(r"\s+", " ", clean_name).upper()
                        if lookup_key in items_dict:
                            items_dict[lookup_key]['qty'] += qty
                            items_dict[lookup_key]['total_price'] += price
                        else:
                            items_dict[lookup_key] = {
                                'original_name': clean_name,
                                'qty': qty,
                                'total_price': price
                            }
                    else:
                        unmatched_lines.append(sub_line)
                else:
                    unmatched_lines.append(sub_line)
                
                i += 1

            # Output all consolidated items
            for item in items_dict.values():
                name = item['original_name']
                q_val = item['qty']
                tot_p = item['total_price']

                price_str = f" - ${tot_p:.2f}" if tot_p > 0 else ""
                qty_str = f" (Qty: {q_val})" if q_val > 1 else ""
                output_lines.append(f"  - {name}{price_str}{qty_str}")

            # Output any unmatched lines so zero data is lost
            for un_line in unmatched_lines:
                output_lines.append(un_line)

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

    # Split cleanly by 50-dash separator boundary
    raw_blocks = re.split(r"-{50,}", content)

    receipt_blocks = []
    seen_fingerprints = set()

    for block in raw_blocks:
        block_str = block.strip()
        if not block_str or "BATCH RUN DATE" in block_str:
            continue

        # Step 1: Consolidate items without dropping any lines
        consolidated_block = consolidate_block_items(block_str)

        # Step 2: Strict Deduplication Fingerprint (Vendor + Date + Amount)
        vendor_m = re.search(r"Vendor:\s*(.*)", consolidated_block)
        date_m = re.search(r"Receipt Date:\s*(.*)", consolidated_block)
        amount_m = re.search(r"Amount:\s*(.*)", consolidated_block)

        if vendor_m and date_m and amount_m:
            fingerprint = f"{vendor_m.group(1).strip().lower()}|{date_m.group(1).strip().lower()}|{amount_m.group(1).strip().lower()}"
        else:
            fingerprint = re.sub(r"\s+", "", consolidated_block).lower()

        if fingerprint in seen_fingerprints:
            continue

        seen_fingerprints.add(fingerprint)
        receipt_blocks.append(consolidated_block)

    # Helper function to sort chronologically by Receipt Date
    def get_sorting_date(block_text):
        match = re.search(r"Receipt Date:\s*([\d:\s\w\[\]\-]+)", block_text)
        if match:
            date_str = match.group(1).strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
                return date_str
        return "9999-99-99"

    receipt_blocks.sort(key=get_sorting_date)

    # Rewrite output back to Receipt_Data.txt
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        for block in receipt_blocks:
            f.write(block + "\n--------------------------------------------------\n")

    print(f"Successfully processed {len(receipt_blocks)} receipt blocks.")


if __name__ == "__main__":
    sort_and_deduplicate_receipt_file()
