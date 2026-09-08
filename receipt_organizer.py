import os
import re

DATA_FILE = "Receipt_Data.txt"

def sort_receipt_file():
    if not os.path.exists(DATA_FILE):
        print(f"File {DATA_FILE} not found. Skipping sorting.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Split content into header and receipt entry blocks
    # Header covers run dates/metadata before individual receipt entries
    blocks = content.split("--------------------------------------------------\n")
    
    header_lines = []
    receipt_blocks = []

    for block in blocks:
        if not block.strip():
            continue
        
        # Check if the block contains a parsed receipt date
        date_match = re.search(r"Receipt Date:\s*([^\n]+)", block)
        if date_match:
            date_str = date_match.group(1).strip()
            # Extract standard YYYY-MM-DD for sorting; fallback to late date if unparsed
            standard_date_match = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
            sort_key = standard_date_match.group(0) if standard_date_match else "9999-99-99"
            
            receipt_blocks.append({
                "sort_key": sort_key,
                "text": block.strip() + "\n--------------------------------------------------\n"
            })
        else:
            # Retain non-receipt header or summary lines
            header_lines.append(block)

    if not receipt_blocks:
        print("No receipt entries found to sort.")
        return

    # Sort receipt blocks by date string in ascending order (earliest first)
    receipt_blocks.sort(key=lambda x: x["sort_key"])

    # Reconstruct the organized text file
    sorted_content = ""
    
    # Re-attach any header lines at top if present
    for h in header_lines:
        sorted_content += h.strip() + "\n--------------------------------------------------\n"
        
    for r in receipt_blocks:
        sorted_content += r["text"]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        f.write(sorted_content)

    print(f"Successfully organized {len(receipt_blocks)} receipt records chronologically in {DATA_FILE}.")

if __name__ == "__main__":
    sort_receipt_file()
