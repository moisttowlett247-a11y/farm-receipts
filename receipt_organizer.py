import os
import re

DATA_FILE = "Receipt_Data.txt"

def sort_and_deduplicate_receipt_file():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found. Skipping processing.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Split content by horizontal rule separator
    raw_blocks = content.split("--------------------------------------------------")
    
    receipt_blocks = []
    seen_fingerprints = set()
    duplicate_count = 0

    for block in raw_blocks:
        cleaned_block = block.strip()
        if not cleaned_block:
            continue
        
        # Clean out old BATCH RUN DATE banners and equality separators
        cleaned_block = re.sub(r"=+\s*BATCH RUN DATE:[^\n]+\s*=+", "", cleaned_block, flags=re.IGNORECASE).strip()
        cleaned_block = re.sub(r"^\s*=+\s*$", "", cleaned_block, flags=re.MULTILINE).strip()
        
        if not cleaned_block:
            continue

        # Extract core transaction values
        vendor = re.search(r"Vendor:\s*([^\n]+)", cleaned_block)
        date = re.search(r"Receipt Date:\s*([^\n]+)", cleaned_block)
        amount = re.search(r"Amount:\s*([^\n]+)", cleaned_block)

        v_str = vendor.group(1).strip().lower() if vendor else ""
        d_str = date.group(1).strip().lower() if date else ""
        a_str = amount.group(1).strip().lower() if amount else ""

        # Normalize amount to numbers only (e.g., '$145.50' -> '145.50')
        a_clean = re.sub(r"[^\d.]", "", a_str)

        # Build fingerprint strictly from Vendor + Date + Amount
        if d_str and a_clean:
            fingerprint = f"{v_str}|{d_str}|{a_clean}"
            
            if fingerprint in seen_fingerprints:
                duplicate_count += 1
                continue
            
            seen_fingerprints.add(fingerprint)

            # Extract standard YYYY-MM-DD for chronological sorting
            standard_date_match = re.search(r"\d{4}-\d{2}-\d{2}", d_str)
            sort_key = standard_date_match.group(0) if standard_date_match else "9999-99-99"
            
            receipt_blocks.append({
                "sort_key": sort_key,
                "text": cleaned_block + "\n--------------------------------------------------\n"
            })

    if not receipt_blocks:
        print("No receipt entries found to process.")
        return

    # Sort receipt blocks chronologically (earliest date first)
    receipt_blocks.sort(key=lambda x: x["sort_key"])

    # Reconstruct clean file output
    sorted_content = "".join(r["text"] for r in receipt_blocks)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        f.write(sorted_content)

    print(f"Done: Retained {len(receipt_blocks)} unique receipt(s) (removed {duplicate_count} duplicate(s)).")

if __name__ == "__main__":
    sort_and_deduplicate_receipt_file()
