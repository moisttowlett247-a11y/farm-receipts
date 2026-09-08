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
socket.setdefaulttimeout(60.0)  # Increased timeout for slow image streams

import requests
from PIL import Image, ImageOps

# Disable PIL image size limit warnings for fast memory processing
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
# ROBUST IMAP ATTACHMENT STREAMER
# =====================================================================
def download_new_receipts():
    """Fetches unseen emails efficiently using standard IMAP fetch with timeout protection."""
    import imaplib

    print(f"[{time.strftime('%H:%M:%S')}] Connecting to IMAP server...", flush=True)
    saved_in_memory_images = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        
        # Fast search index using UNSEEN directly
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
                # 1. Fetch Header first to verify sender quickly
                status, fetch_header = mail.uid('fetch', u_id, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
                if status == 'OK' and fetch_header and TRUSTED_SENDERS:
                    raw_header = str(fetch_header).lower()
                    sender_matched = any(sender in raw_header for sender in TRUSTED_SENDERS)
                    if not sender_matched:
                        print(f"[{time.strftime('%H:%M:%S')}] UID {u_id} skipped: Sender not in TRUSTED_SENDERS.", flush=True)
                        continue

                # 2. Fetch full payload
                print(f"[{time.strftime('%H:%M:%S')}] Fetching payload for UID {u_id}...", flush=True)
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
                                    # Auto-rotate phone camera orientation metadata before resizing
                                    pil_image = ImageOps.exif_transpose(pil_image)
                                    pil_image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
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
                                # Auto-rotate phone camera orientation metadata before resizing
                                pil_image = ImageOps.exif_transpose(pil_image)
                                pil_image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
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
# THREAD-ISOLATED VISION ENGINE (DIRECT REST API USING GEMINI 3.5 LITE)
# =====================================================================
def analyze_image_with_gemini(img_obj, assigned_key, max_fast_retries=1):
    """Processes image directly over REST using Gemini 3.5 Lite."""
    buffer = io.BytesIO()
    img_obj.save(buffer, format="JPEG", quality=85)
    img_bytes = buffer.getvalue()
    base64_image = base64.b64encode(img_bytes).decode('utf-8')

    # REST endpoint configured explicitly for gemini-3.5-lite
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-lite:generateContent?key={assigned_key}"

    prompt = (
        "Analyze this image carefully. It may contain ONE single receipt OR MULTIPLE distinct receipts placed side-by-side or stacked.\n"
        "Extract data for EACH distinct receipt visible in the image as an object inside the 'receipts' array.\n\n"
        "CRITICAL MULTI-RECEIPT & ANTI-DUPLICATION RULES:\n"
        "- If multiple receipts are arranged horizontally side-by-side or in landscape photo mode, evaluate each physical paper strip as its own SEPARATE receipt.\n"
        "- Scan strictly left-to-right (or top-to-bottom) and track distinct grand total/header boundaries to NEVER log the same physical receipt twice.\n"
        "- Read all text according to its proper upright reading direction.\n\n"
        "CRITICAL ACCURACY RULES:\n"
        "1. VENDOR IDENTIFICATION:\n"
        "   - Identify the primary business or store name printed at the top as 'vendor'.\n"
        "   - Clean up branding slogans (e.g., return 'Walmart', not 'Walmart Save Money Live Better').\n\n"
        "2. GRAND TOTAL ACCURACY:\n"
        "   - Look for labels like 'BALANCE DUE', 'TOTAL', 'GRAND TOTAL', or 'AMOUNT PAID'.\n"
        "   - Do NOT select 'SUBTOTAL', 'TAX', 'CHANGE DUE', or individual item prices as the grand total.\n"
        "   - Return ONLY the raw floating-point number string (e.g., '142.50') without currency symbols ($) or commas.\n\n"
        "3. CATEGORIZATION:\n"
        "   - Assign 'category' to EXACTLY one of these three options based on item content:\n"
        "     * 'Farm:Cows' (feed, cattle equipment, vet supplies, fence posts, mineral blocks)\n"
        "     * 'Farm:Chickens' (poultry feed, coops, heat lamps, egg cartons)\n"
        "     * 'Farm:General' (tools, general hardware, fuel, office supplies, household goods, or mixed items)\n\n"
        "4. DATE EXTRACTION & OCR PRECISION:\n"
        "   - Extract the purchase/transaction date as 'date' (preferred format: YYYY-MM-DD).\n"
        "   - CAUTION ON YEAR DIGITS: Thermal receipts frequently blur numbers. Carefully inspect the individual strokes of the year.\n"
        "     * Do not confuse '2026', '2025', '2024', '2023', or '2022'. Extract the EXACT year printed on the receipt.\n"
        "   - CAUTION ON DATE FORMATS: If a receipt lists '08/04/24', look at cashier timestamps or neighboring text to distinguish MM/DD/YY from DD/MM/YY. If ambiguous, preserve the exact printed text string.\n\n"
        "5. ITEMIZED LINE ITEMS:\n"
        "   - Extract all individual purchased products into the 'items' array.\n"
        "   - For each item:\n"
        "     * 'name': full product description or SKU title.\n"
        "     * 'price': line item price (no currency symbols). If discounts/savings are shown below an item, record the final net price paid.\n"
        "     * 'weight': item weight or bulk weight ONLY (e.g., '50 lbs', '2.5 kg'). DO NOT place unit pricing or quantity math (e.g., '1 x $26') here; return empty string '' if no physical weight is listed."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64_image
                        }
                    }
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
                                "date": {"type": "STRING"},
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
                            "required": ["vendor", "total", "category", "date", "items"]
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
            response = requests.post(url, headers=headers, json=payload, timeout=20)
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
            receipt_date = receipt.get('date', '[Date Not Found]')
            items_list = receipt.get('items', [])
            
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
