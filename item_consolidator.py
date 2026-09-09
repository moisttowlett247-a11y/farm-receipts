import os
import re

DATA_FILE = "Receipt_Data.txt"

KNOWN_HEADERS = {
    "file name:", "category:", "vendor:", "receipt date:", 
    "date source line:", "payment method:", "reference #:", 
    "subtotal:", "sales tax:", "amount:", "items:"
}

def process_items_in_block(block_text):
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
                
                # Exit item scope on section boundary or known header key
                if stripped_sub.startswith("----------------") or stripped_sub.startswith("============="):
                    break
                
                key_prefix = stripped_sub.split(":", 1)[0].lower() + ":" if ":" in stripped_sub else ""
                if key_prefix in KNOWN_HEADERS:
                    break

                if sub_line.startswith("  - ") or sub_line.startswith("- "):
                    barcode_match = re.search(r"\b(\d{8,14})\b", sub_line)
                    
                    if barcode_match:
                        barcode = barcode_match.group(1)
                        
                        price_match = re.search(r"\$\s*(\d+(?:\.\d+)?)", sub_line)
                        price = float(price_match.group(1)) if price_match else 0.0
                        
                        qty_match = re.search(r"\(Qty:\s*(\d+)\)", sub_line, re.IGNORECASE)
                        qty = int(qty_match.group(1)) if qty_match else 1
                        
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

            # Output barcode items consolidated
            for item in seen_barcodes.values():
                base_text = item['base_text']
                tot_p = item['total_price']
                q_val = item['qty']
                
                price_str = f" - ${tot_p:.2f}" if tot_p > 0 else ""
                qty_str = f" (Qty: {q_val})" if q_val > 1 else ""
                output_lines.append(f"{base_text}{price_str}{qty_str}")

            # Append any non-barcode items as-is
            for item_line in non_barcode_items:
                output_lines.append(item_line)

        else:
            output_lines.append(line)
            i += 1

    return "\n".join(output_lines)


def consolidate_receipt_items():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        print("Receipt data empty.")
        return

    blocks = re.split(r"-{50,}", content)
    updated_blocks = []

    for block in blocks:
        block_str = block.strip()
        if not block_str or "BATCH RUN DATE" in block_str:
            continue
        updated_blocks.append(process_items_in_block(block_str))

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        for block in updated_blocks:
            f.write(block + "\n--------------------------------------------------\n")

    print(f"Consolidated items across {len(updated_blocks)} receipt blocks.")


if __name__ == "__main__":
    consolidate_receipt_items()
