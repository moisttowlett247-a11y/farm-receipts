import os
import re

DATA_FILE = "Receipt_Data.txt"

def sort_and_deduplicate_receipt_file():
    if not os.path.exists(DATA_FILE):
        open(DATA_FILE, "a", encoding="utf-8").close()
        print(f"Created empty {DATA_FILE} because it did not exist.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        print(f"{DATA_FILE} is empty. Nothing to sort.")
        return

    raw_blocks = re.split(r"-{50,}", content)

    receipt_blocks = []
    seen_fingerprints = set()

    for idx, block in enumerate(raw_blocks):
        block_str = block.strip()
        if not block_str or "BATCH RUN DATE" in block_str:
            continue

        # Extract specific identifying markers
        file_m = re.search(r"File Name:\s*(.*)", block_str, re.IGNORECASE)
        ref_m = re.search(r"Reference #:\s*(.*)", block_str, re.IGNORECASE)
        amount_m = re.search(r"Amount:\s*(.*)", block_str, re.IGNORECASE)
        date_m = re.search(r"Receipt Date:\s*(.*)", block_str, re.IGNORECASE)

        file_id = file_m.group(1).strip().lower() if file_m else ""
        ref_id = ref_m.group(1).strip().lower() if ref_m else ""
        amount_id = amount_m.group(1).strip().lower() if amount_m else ""
        date_id = date_m.group(1).strip().lower() if date_id else ""

        # CRITICAL FIX: Preserve the full multi-receipt filename suffix (_r1, _r2, etc.) 
        # so different receipts extracted from the same image are treated as separate files.
        if file_id:
            fingerprint = f"file:{file_id}|amount:{amount_id}|date:{date_id}"
        elif ref_id:
            fingerprint = f"ref:{ref_id}"
        else:
            # Fallback: Hash the entire unique text body of this specific receipt block
            fingerprint = f"block:{re.sub(r'\s+', '', block_str).lower()}"

        if fingerprint in seen_fingerprints:
            print(f"Skipping true duplicate receipt block: {file_id}")
            continue

        seen_fingerprints.add(fingerprint)
        receipt_blocks.append(block_str)

    def get_sorting_date(block_text):
        match = re.search(r"Receipt Date:\s*([\d:\s\w\[\]\-]+)", block_text, re.IGNORECASE)
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
