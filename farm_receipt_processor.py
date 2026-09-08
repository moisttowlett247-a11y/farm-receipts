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
# IMAGE PREPROCESSING FOR OCR & DATES
# =====================================================================
def prepare_image_variants(pil_image):
    """Prepares high-contrast full image along with top/bottom crop zooms for high-precision date OCR."""
    pil_image = ImageOps.exif_transpose(pil_image)
    
    # Enhance contrast and sharpness for thermal print
    enhancer = ImageEnhance.Contrast(pil_image)
    enhanced_img = enhancer.enhance(1.6)
    
    # Resize standard full image
    full_img = enhanced_img.copy()
    full_img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    
    buffer = io.BytesIO()
    full_img.save(buffer, format="JPEG", quality=92)
    full_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    # Crop Top 30% (Header/Date region)
    width, height = enhanced_img.size
    top_crop = enhanced_img.crop((0, 0, width, int(height * 0.30)))
    top_crop.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buf_top = io.BytesIO()
    top_crop.save(buf_top, format="JPEG", quality=92)
    top_b64 = base64.b64encode(buf_top.getvalue()).decode('utf-8')

    # Crop Bottom 30% (Footer/Terminal/Date region)
    bottom_crop = enhanced_img.crop((0, int(height * 0.70), width, height))
    bottom_crop.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buf_bot = io.BytesIO()
    bottom_crop.save(buf_bot, format="JPEG", quality=92)
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
    """Processes receipt images with dedicated high-contrast date crops and explicit digit separation."""
    full_b64, top_b64, bot_b64 = prepare_image_variants(img_obj)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={assigned_key}"

    prompt = (
        "You are provided with three image inputs of the receipt(s):\n"
        "1. Full receipt image.\n"
        "2. Zoomed high-contrast crop of the TOP section (Header).\n"
        "3. Zoomed high-contrast crop of the BOTTOM section (Footer).\n\n"
        "CRITICAL TAX-GRADE DATE EXTRACTION INSTRUCTIONS:\n"
        "- Locate the printed transaction date in the header or footer.\n"
        "- Receipt dates strictly follow US standard formatting: MM/DD/YY or MM/DD/YYYY.\n"
        "- The FIRST number is ALWAYS the month (1-12).\n"
        "- The SECOND number is ALWAYS the day (1-31).\n"
        "- The THIRD number is ALWAYS the year (2-digit or 4-digit, e.g., '26' means 2026, '24' means 2024).\n"
        "- Check for thermal ink distortion: Faded or light loop digits like '08' can look like '09' or '00'. Examine pixel boundaries carefully before outputting digits.\n"
        "- Extract the exact raw text line where the date appears into 'raw_date_text' (e.g., '08/24/26').\n"
        "- Return the extracted numbers strictly as individual string values for 'month', 'day', and 'year'. Do NOT swap digits.\n"
        "- If the date is unreadable or absent, return empty strings for month, day, year.\n\n"
        "OTHER EXTRACTION RULES:\n"
        "- Identify store 'vendor' name (e.g., 'Walmart').\n"
        "- Extract grand total as float string (e.g., '145.50'). Do NOT use subtotals or tax.\n"
        "- Assign 'category' strictly to 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'.\n"
        "- Extract line items into 'items' array (name, price, weight)."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": full_b64}},
                    {"inline_data": {"mime_type": "image/jpeg", "data": top_b64}},
                    {"inline_data": {"mime_type": "image/jpeg", "data": bot_b64}}
                ]
            }
        ],
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
                                "total": {"type": "STRING"},
                                "category": {"type": "STRING"},
                                "month": {"type": "STRING", "description": "2-digit month e.g. '08'"},
                                "day": {"type": "STRING", "description": "2-digit day e.g. '24'"},
                                "year": {"type": "STRING", "description": "2-digit or 4-digit year e.g. '26' or '2026'"},
                                "raw_date_text": {"type": "STRING"},
                                "items": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "name": {"type": "STRING"},
                                            "price": {"type": "STRING"},
                                            "weight": {"type": "STRING"}
                                        },
                                        "required": ["name", "price", "weight"]
                                    }
                                }
                            },
                            "required": ["vendor", "total", "category", "month", "day", "year", "raw_date_text", "items"]
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
            total = receipt.get('total', '[Amount Not Found]')
            raw_date_line = receipt.get('raw_date_text', '').strip()
            items_list = receipt.get('items', [])
            
            # 1. Attempt regex extraction directly from raw printed text line
            regex_match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', raw_date_line)

            if regex_match:
                m_str, d_str, y_str = regex_match.groups()
                month = m_str.zfill(2)
                day = d_str.zfill(2)
                year = y_str
                if len(year) == 2:
                    year = f"20{year}"
                receipt_date = f"{year}-{month}-{day}"
            else:
                # 2. Fall back to structured LLM fields
                month = receipt.get('month', '').strip().zfill(2)
                day = receipt.get('day', '').strip().zfill(2)
                year = receipt.get('year', '').strip()
                if len(year) == 2:
                    year = f"20{year}"

                if month and day and len(year) == 4 and month.isdigit() and day.isdigit() and year.isdigit():
                    receipt_date = f"{year}-{month}-{day}"
                else:
                    receipt_date = f"[VERIFY: {raw_date_line or 'Date Unclear'}]"

            if total and not str(total).startswith('$'):
                total = f"${total}"
                
            item_lines = []
            if items_list:
                for item in items_list:
                    if isinstance(item, dict):
                        name = item.get('name', 'Unknown Item')
                        price = item.get('price', '').strip()
                        weight = item.get('weight', '').strip()
                        
                        price_str = f" - ${price}" if price else ""
                        weight_str = f" ({weight})" if weight else ""
                        item_lines.append(f"  - {name}{price_str}{weight_str}\n")
                    else:
                        item_lines.append(f"  - {item}\n")
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
