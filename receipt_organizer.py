import os
import re

DATA_FILE = "Receipt_Data.txt"

def sort_and_deduplicate_receipt_file():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found. Skipping processing.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Split content by the horizontal rule separator
    raw_blocks = content.split("--------------------------------------------------")
    
    receipt_blocks = []
    seen_fingerprints = set()
    duplicate_count = 0

    for block in raw_blocks:
        cleaned_block = block.strip()
        if not cleaned_block:
            continue
        
        # Strip out old BATCH RUN DATE banners so they don't break block formatting
        cleaned_block = re.sub(r"=+\nBATCH RUN DATE:[^\n]+\n=+", "", cleaned_block).strip()
        if not cleaned_block:
            continue

        # Extract Receipt Date for sorting
        date_match = re.search(r"Receipt Date:\s*([^\n]+)", cleaned_block)
        
        if date_match:
            # Generate a normalized fingerprint based strictly on receipt content
            fingerprint = re.sub(r"\s+", " ", cleaned_block)
            
            if fingerprint in seen_fingerprints:
                duplicate_count += 1
                continue
            
            seen_fingerprints.add(fingerprint)

            date_str = date_match.group(1).strip()
            standard_date_match = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
            sort_key = standard_date_match.group(0) if standard_date_match else "9999-99-99"
            
            receipt_blocks.append({
                "sort_key": sort_key,
                "text": cleaned_block + "\n--------------------------------------------------\n"
            })

    if not receipt_blocks:
        print("No receipt entries found to process.")
        return

    # Sort receipt blocks chronologically (earliest first)
    receipt_blocks.sort(key=lambda x: x["sort_key"])

    # Reconstruct output cleanly
    sorted_content = "".join(r["text"] for r in receipt_blocks)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        f.write(sorted_content)

    print(f"Successfully organized {len(receipt_blocks)} unique receipt records (removed {duplicate_count} duplicates).")

if __name__ == "__main__":
    sort_and_deduplicate_receipt_file()
