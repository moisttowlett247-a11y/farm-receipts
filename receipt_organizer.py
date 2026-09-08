import os
import re

DATA_FILE = "Receipt_Data.txt"

def sort_and_deduplicate_receipt_file():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found. Skipping processing.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Split content into distinct blocks
    blocks = content.split("--------------------------------------------------\n")
    
    header_lines = []
    receipt_blocks = []
    seen_fingerprints = set()
    duplicate_count = 0

    for block in blocks:
        cleaned_block = block.strip()
        if not cleaned_block:
            continue
        
        # Check if the block contains a parsed receipt date
        date_match = re.search(r"Receipt Date:\s*([^\n]+)", cleaned_block)
        
        if date_match:
            # Generate a unique fingerprint based on the full text content of the block
            # Normalizing spaces avoids accidental duplication due to formatting shifts
            fingerprint = re.sub(r"\s+", " ", cleaned_block)
            
            if fingerprint in seen_fingerprints:
                duplicate_count += 1
                continue
            
            seen_fingerprints.add(fingerprint)

            # Extract date for sorting
            date_str = date_match.group(1).strip()
            standard_date_match = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
            sort_key = standard_date_match.group(0) if standard_date_match else "9999-99-99"
            
            receipt_blocks.append({
                "sort_key": sort_key,
                "text": cleaned_block + "\n--------------------------------------------------\n"
            })
        else:
            # Retain batch run dividers or top-level log headers
            if "BATCH RUN DATE:" not in cleaned_block and "==================================================" not in cleaned_block:
                header_lines.append(cleaned_block)

    if not receipt_blocks:
        print("No receipt entries found to process.")
        return

    # Sort receipt blocks by date string in ascending order (earliest first)
    receipt_blocks.sort(key=lambda x: x["sort_key"])

    # Reconstruct file output
    sorted_content = ""
    for h in header_lines:
        sorted_content += h + "\n--------------------------------------------------\n"
        
    for r in receipt_blocks:
        sorted_content += r["text"]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        f.write(sorted_content)

    print(f"Done: Retained {len(receipt_blocks)} unique receipts (removed {duplicate_count} duplicate(s)). Sorted chronologically.")

if __name__ == "__main__":
    sort_and_deduplicate_receipt_file()
