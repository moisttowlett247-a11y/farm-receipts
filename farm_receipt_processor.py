import sys
import time
print(f"[{time.strftime('%H:%M:%S')}] Python process started...", flush=True)
import os
import re
import json
import io
import base64
import email
import concurrent.futures
from datetime import datetime
print(f"[{time.strftime('%H:%M:%S')}] Standard libraries loaded.", flush=True)

import socket
socket.setdefaulttimeout(60.0)
import requests
from PIL import Image, ImageOps, ImageEnhance
Image.MAX_IMAGE_PIXELS = None
print(f"[{time.strftime('%H:%M:%S')}] Third-party dependencies loaded.", flush=True)

# =====================================================================
# CONFIGURATION & KEY MANAGER
# =====================================================================
EMAIL_USER = os.getenv("EMAIL_USER", "your_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_app_password")
IMAP_SERVER = "imap.gmail.com"

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

TRUSTED_SENDERS_RAW = os.getenv("TRUSTED_SENDERS", "")
TRUSTED_SENDERS = [e.strip().lower() for e in TRUSTED_SENDERS_RAW.split(",") if e.strip()]

def setup_folders():
    return "."

# =====================================================================
# IMAGE PREPROCESSING FOR OCR & THERMAL TEXT
# =====================================================================
def prepare_image_variants(pil_image):
    """Prepares standard full image alongside threshold-enhanced crops for faint thermal receipt text."""
    pil_image = ImageOps.exif_transpose(pil_image)

    # Standard full image
    full_img = pil_image.copy()
    full_img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    buf_full = io.BytesIO()
    full_img.save(buf_full, format="JPEG", quality=92)
    full_b64 = base64.b64encode(buf_full.getvalue()).decode('utf-8')

    # Enhanced Grayscale Thresholding for thermal print (Top & Bottom crops)
    def enhance_crop(crop_img):
        gray = crop_img.convert("L")
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(2.5)
        sharpener = ImageEnhance.Sharpness(enhanced)
        return sharpener.enhance(2.0)

    width, height = pil_image.size
    top_crop = enhance_crop(pil_image.crop((0, 0, width, int(height * 0.35))))
    top_crop.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buf_top = io.BytesIO()
    top_crop.save(buf_top, format="JPEG", quality=95)
    top_b64 = base64.b64encode(buf_top.getvalue()).decode('utf-8')

    bottom_crop = enhance_crop(pil_image.crop((0, int(height * 0.65), width, height)))
    bottom_crop.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buf_bot = io.BytesIO()
    bottom_crop.save(buf_bot, format="JPEG", quality=95)
    bot_b64 = base64.b64encode(buf_bot.getvalue()).decode('utf-8')

    return full_b64, top_b64, bot_b64

# =====================================================================
# ROBUST IMAP ATTACHMENT STREAMER
# =====================================================================
def download_new_receipts():
    import imaplib
    print(f"[{time.strftime('%H:%M:%S')}] Connecting to IMAP server...", flush=True)
    saved_in_memory_images = []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")

        status, data = mail.uid('search', None, 'UNSEEN')
        if status != 'OK' or not data or not data[0]:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass
            return None, []

        email_uids = data[0].decode('utf-8').split()
        print(f"[{time.strftime('%H:%M:%S')}] Found {len(email_uids)} unseen email(s). Processing...", flush=True)

        for u_id in email_uids:
            try:
                status, fetch_header = mail.uid('fetch', u_id, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
                if status == 'OK' and fetch_header and TRUSTED_SENDERS:
                    raw_header = str(fetch_header).lower()
                    sender_matched = any(sender in raw_header for sender in TRUSTED_SENDERS)
                    if not sender_matched:
                        print(f"[{time.strftime('%H:%M:%S')}] UID {u_id} skipped: Sender not in TRUSTED_SENDERS.", flush=True)
                        continue

                status, fetch_data = mail.uid('fetch', u_id, '(BODY.PEEK[])')
                if status != 'OK' or not fetch_data:
                    continue

                msg = None
                for block in fetch_data:
                    if isinstance(block, tuple) and len(block) > 1:
                        msg = email.message_from_bytes(block[1])
                        break

                if not msg:
                    continue

                attachments_in_msg = []
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type().lower()
                        filename = part.get_filename() or ""
                        if "image" in content_type or filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
                            image_bytes = part.get_payload(decode=True)
                            if image_bytes:
                                try:
                                    pil_image = Image.open(io.BytesIO(image_bytes))
                                    attachments_in_msg.append({
                                        "image_object": pil_image,
                                        "original_name": filename or f"receipt_{u_id}.jpg",
                                    })
                                except Exception as img_err:
                                    print(f"[{time.strftime('%H:%M:%S')}] Failed to parse image on UID {u_id}: {img_err}", flush=True)
                else:
                    if "image" in msg.get_content_type().lower():
                        image_bytes = msg.get_payload(decode=True)
                        if image_bytes:
                            try:
                                pil_image = Image.open(io.BytesIO(image_bytes))
                                attachments_in_msg.append({
                                    "image_object": pil_image,
                                    "original_name": f"receipt_{u_id}.jpg",
                                })
                            except Exception:
                                pass

                if attachments_in_msg:
                    saved_in_memory_images.append({
                        "u_id": u_id,
                        "attachments": attachments_in_msg,
                    })
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] UID {u_id} skipped: No valid image attachments.", flush=True)

            except socket.timeout:
                print(f"[{time.strftime('%H:%M:%S')}] Timeout error fetching UID {u_id}. Retrying on next run.", flush=True)
            except Exception as uid_err:
                print(f"[{time.strftime('%H:%M:%S')}] Error processing UID {u_id}: {uid_err}", flush=True)

        if saved_in_memory_images:
            return mail, saved_in_memory_images
        else:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass

    except Exception as e:
        print(f"Inbox processing warning/error: {e}", flush=True)

    return None, []

# =====================================================================
# THREAD-ISOLATED VISION ENGINE (GEMINI 3.5 FLASH LITE WITH MULTI-CROP ANALYSIS)
# =====================================================================
def analyze_image_with_gemini(img_obj, assigned_key, max_fast_retries=1):
    """Processes receipt images with high-contrast date crops, strict cross-verification, and QuickBooks extraction."""
    full_b64, top_b64, bot_b64 = prepare_image_variants(img_obj)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={assigned_key}"

    prompt = (
        "You are provided with three image inputs of the receipt(s):\n"
        "1. Full receipt image.\n"
        "2. High-contrast threshold crop of the TOP section (Header).\n"
        "3. High-contrast threshold crop of the BOTTOM section (Footer).\n\n"
        "DATE EXTRACTION & CROSS-VERIFICATION INSTRUCTIONS:\n"
        "- Receipts are dated 2025 or later.\n"
        "- Locate the printed transaction timestamp on the receipt (Header or Footer).\n"
        "- Standard US date format: MM/DD/YY or MM/DD/YYYY.\n"
        "- Month is 01-12, Day is 01-31, Year is 2025 or later (e.g., '25' = 2025, '26' = 2026).\n"
        "- THERMAL DISTORTION CHECK: Thermal printing often blurs '8' into '9' or '0'. Carefully inspect the loops on the high-contrast crops before choosing digits.\n"
        "- CROSS-CHECK: Verify the date against receipt transaction lines (e.g., 'ST# ... TE# ... 08/24/25').\n"
        "- Extract raw_date_text exactly as printed (e.g., '08/24/25'). Do NOT swap month/day positions.\n"
        "- Return month, day, and 4-digit year as separate strings.\n\n"
        "QUICKBOOKS ACCOUNTING EXTRACTION RULES:\n"
        "- Identify store 'vendor' name (e.g., 'Walmart').\n"
        "- Assign 'category' strictly to 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'.\n"
        "- Extract payment_method (e.g., 'Visa ending in 4321', 'Cash', 'Checking'). Default to 'Not Specified' if not found.\n"
        "- Extract ref_number (Order #, Invoice #, or Trans ID). Default to 'N/A' if not found.\n"
        "- Extract subtotal as float string (e.g., '140.00').\n"
        "- Extract sales_tax as float string (e.g., '5.50').\n"
        "- Extract total (grand total) as float string (e.g., '145.50'). Do NOT confuse subtotal with total.\n"
        "- Extract line items into 'items' array.\n"
        "- ITEM EXTRACTION RULE: Extract every printed item on the receipt. For 'qty', extract printed multipliers if present (e.g. if '2 @ 0.44' appears, set qty to '2'). Set 'price' to the total line price printed for that entry.\n"
        "- CLEAN ITEM DESCRIPTION: Exclude store barcode numbers, UPC codes, or tax flags from the item name."
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": full_b64}},
                {"inline_data": {"mime_type": "image/jpeg", "data": top_b64}},
                {"inline_data": {"mime_type": "image/jpeg", "data": bot_b64}}
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.0,
            "response_schema": {
                "type": "OBJECT",
                "properties": {
                    "receipts": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "vendor": {"type": "STRING"},
                                "category": {"type": "STRING"},
                                "payment_method": {"type": "STRING"},
                                "ref_number": {"type": "STRING"},
                                "subtotal": {"type": "STRING"},
                                "sales_tax": {"type": "STRING"},
                                "total": {"type": "STRING"},
                                "month": {"type": "STRING", "description": "2-digit month e.g. '08'"},
                                "day": {"type": "STRING", "description": "2-digit day e.g. '24'"},
                                "year": {"type": "STRING", "description": "4-digit year e.g. '2025' or '2026'"},
                                "raw_date_text": {"type": "STRING"},
                                "items": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "name": {"type": "STRING", "description": "Clean item description without barcodes"},
                                            "qty": {"type": "STRING", "description": "Quantity purchased, default to '1'"},
                                            "price": {"type": "STRING", "description": "Price for this line item"}
                                        },
                                        "required": ["name", "qty", "price"]
                                    }
                                }
                            },
                            "required": [
                                "vendor", "category", "payment_method", "ref_number", 
                                "subtotal", "sales_tax", "total", "month", "day", "year", 
                                "raw_date_text", "items"
                            ]
                        }
                    }
                },
                "required": ["receipts"]
            }
        }
    }

    headers = {"Content-Type": "application/json"}

    for attempt in range(max_fast_retries + 1):
        try:
            print(f"[{time.strftime('%H:%M:%S')}] Sending request to Gemini REST API...", flush=True)
            response = requests.post(url, headers=headers, json=payload, timeout=25)
            response.raise_for_status()
            res_json = response.json()
            text_response = res_json['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text_response.strip())
        except Exception as e:
            if attempt < max_fast_retries:
                time.sleep(1)
            else:
                print(f"REST API error for attachment: {e}", flush=True)
                return None

# =====================================================================
# WORKER SCOPE ROUTER
# =====================================================================
def process_single_email_group(args):
    email_package, assigned_key = args
    attachments = email_package["attachments"]
    u_id = email_package["u_id"]

    gathered_log_blocks = []

    for attachment in attachments:
        img_obj = attachment["image_object"]
        filename = attachment["original_name"]

        data = analyze_image_with_gemini(img_obj, assigned_key)
        img_obj.close()

        if not data or "receipts" not in data:
            continue

        receipts_found = data.get("receipts", [])

        for idx, receipt in enumerate(receipts_found, start=1):
            vendor = re.sub(r'[\\/*?:"<>|]', "", receipt.get('vendor', 'Unknown_Vendor'))[:20].strip()
            category = receipt.get('category', 'Farm:General')
            payment_method = receipt.get('payment_method', 'Not Specified').strip()
            ref_number = receipt.get('ref_number', 'N/A').strip()
            subtotal = receipt.get('subtotal', '').strip()
            sales_tax = receipt.get('sales_tax', '').strip()
            total = receipt.get('total', '[Amount Not Found]').strip()
            raw_date_line = receipt.get('raw_date_text', '').strip()
            raw_items_list = receipt.get('items', [])

            # --- PYTHON-LEVEL CONSOLIDATION & DEDUPLICATION ENGINE ---
            consolidated_items = {}
            for item in raw_items_list:
                if isinstance(item, dict):
                    raw_name = item.get('name', 'Unknown Item').strip()
                    clean_name = re.sub(r'\s+', ' ', raw_name).upper()
                    
                    try:
                        q_val = int(re.sub(r'[^\d]', '', str(item.get('qty', '1'))))
                    except ValueError:
                        q_val = 1
                    if q_val < 1:
                        q_val = 1

                    raw_price = str(item.get('price', '0')).replace('$', '').strip()
                    try:
                        p_val = float(re.sub(r'[^\d.]', '', raw_price))
                    except ValueError:
                        p_val = 0.0

                    if clean_name in consolidated_items:
                        consolidated_items[clean_name]['qty'] += q_val
                        consolidated_items[clean_name]['total_price'] += p_val
                    else:
                        consolidated_items[clean_name] = {
                            'name': raw_name,
                            'qty': q_val,
                            'total_price': p_val
                        }

            # --- MULTI-YEAR DATE EXTRACTION & VALIDATION ENGINE ---
            receipt_date = None

            # Attempt 1: Regex parse from raw text string
            regex_match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', raw_date_line)
            if regex_match:
                m_str, d_str, y_str = regex_match.groups()
                m_int, d_int = int(m_str), int(d_str)
                if len(y_str) == 2:
                    y_str = f"20{y_str}"
                y_int = int(y_str)

                # Validate month (1-12), day (1-31), year (2025+)
                if 1 <= m_int <= 12 and 1 <= d_int <= 31 and 2025 <= y_int <= 2030:
                    receipt_date = f"{y_str}-{m_str.zfill(2)}-{d_str.zfill(2)}"

            # Attempt 2: Fall back to structured JSON model fields
            if not receipt_date:
                m_str = receipt.get('month', '').strip().zfill(2)
                d_str = receipt.get('day', '').strip().zfill(2)
                y_str = receipt.get('year', '').strip()
                if len(y_str) == 2:
                    y_str = f"20{y_str}"

                if m_str.isdigit() and d_str.isdigit() and y_str.isdigit():
                    m_int, d_int, y_int = int(m_str), int(d_str), int(y_str)
                    if 1 <= m_int <= 12 and 1 <= d_int <= 31 and 2025 <= y_int <= 2030:
                        receipt_date = f"{y_str}-{m_str.zfill(2)}-{d_str.zfill(2)}"

            # Safety fallback for unparseable dates
            if not receipt_date:
                receipt_date = f"[VERIFY DATE: {raw_date_line or 'Unclear'}]"

            # Currency Formatting
            if total and not str(total).startswith('$'):
                total = f"${total}"
            if subtotal and not str(subtotal).startswith('$'):
                subtotal = f"${subtotal}"
            if sales_tax and not str(sales_tax).startswith('$'):
                sales_tax = f"${sales_tax}"

            item_lines = []
            if consolidated_items:
                for c_item in consolidated_items.values():
                    name = c_item['name']
                    qty = c_item['qty']
                    tot_price = c_item['total_price']

                    price_str = f" - ${tot_price:.2f}" if tot_price > 0 else ""
                    qty_str = f" (Qty: {qty})" if qty > 1 else ""

                    item_lines.append(f"  - {name}{price_str}{qty_str}\n")
                formatted_items = "".join(item_lines)
            else:
                formatted_items = "  - [No Items Found]\n"

            suffix = f"_r{idx}" if len(receipts_found) > 1 else ""
            new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}{suffix}___{filename}"

            log_entry = (
                f"File Name: {new_filename}\n"
                f"Category: {category}\n"
                f"Vendor: {vendor}\n"
                f"Receipt Date: {receipt_date}\n"
                f"Date Source Line: {raw_date_line}\n"
                f"Payment Method: {payment_method}\n"
                f"Reference #: {ref_number}\n"
                f"Subtotal: {subtotal or 'N/A'}\n"
                f"Sales Tax: {sales_tax or '$0.00'}\n"
                f"Amount: {total}\n"
                f"Items:\n{formatted_items}"
                f"--------------------------------------------------\n"
            )
            gathered_log_blocks.append(log_entry)

    has_success = len(gathered_log_blocks) > 0
    return {"u_id": u_id, "success": has_success, "blocks": gathered_log_blocks}

# =====================================================================
# PIPELINE COORDINATOR
# =====================================================================
def process_receipts(mail_session, email_packages, processed_dir):
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    extracted_records = []
    worker_inputs = []

    for idx, package in enumerate(email_packages):
        assigned_key = GEMINI_KEYS[idx % len(GEMINI_KEYS)] if GEMINI_KEYS else None
        worker_inputs.append((package, assigned_key))

    pool_workers = min(len(email_packages) * 2, 10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = {executor.submit(process_single_email_group, w_in): w_in for w_in in worker_inputs}

        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                u_id = result["u_id"]

                if result["success"]:
                    extracted_records.extend(result["blocks"])
                    if mail_session:
                        try:
                            mail_session.uid('store', u_id, '+FLAGS', '\\Seen')
                        except Exception:
                            pass
                else:
                    print(f"⚠️ Total failure on UID {u_id}. Keeping unread.", flush=True)

            except Exception as e:
                print(f"Thread processor error: {e}", flush=True)

    if extracted_records:
        def get_sorting_date(log_text):
            match = re.search(r"Receipt Date:\s*([\d:\s\w\[\]\-]+)", log_text)
            if match:
                date_str = match.group(1).strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
                    return date_str
            return "9999-99-99"

        extracted_records.sort(key=get_sorting_date)

        with open(log_file_path, "a", encoding="utf-8") as log:
            log.write(f"\n==================================================\n")
            log.write(f"BATCH RUN DATE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            log.write(f"==================================================\n")
            log.writelines(extracted_records)

        print(f"[{time.strftime('%H:%M:%S')}] Processed and logged {len(extracted_records)} receipts.", flush=True)

    if mail_session:
        try:
            mail_session.close()
            mail_session.logout()
        except Exception:
            pass

# =====================================================================
# MAIN ENTRY
# =====================================================================
if __name__ == "__main__":
    print(f"[{time.strftime('%H:%M:%S')}] Free Farm Receipt Processor Initialized.", flush=True)
    processed_folder = setup_folders()
    mail_session, email_queue = download_new_receipts()

    if email_queue:
        process_receipts(mail_session, email_queue, processed_folder)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Inbox check clear. No unread receipts found.", flush=True)
