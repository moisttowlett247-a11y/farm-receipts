import sys
import time

print(f"[{time.strftime('%H:%M:%S')}] Python process started...", flush=True)

import os
import re
import json
import io
import base64
import concurrent.futures
from datetime import datetime, timedelta

print(f"[{time.strftime('%H:%M:%S')}] Standard libraries loaded.", flush=True)

import socket
socket.setdefaulttimeout(30.0)

import requests
from PIL import Image

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
# ULTRA-FAST DIRECT ATTACHMENT STREAMER
# =====================================================================
def fetch_attachment_for_uid(u_id):
    """Directly fetches image parts using IMAP BODYSTRUCTURE, bypassing full email parsing."""
    import imaplib

    attachments_in_msg = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")

        # 1. Inspect Header + Structure
        status, fetch_info = mail.uid('fetch', u_id, '(BODY.PEEK[HEADER.FIELDS (FROM)] BODYSTRUCTURE)')
        if status != 'OK' or not fetch_info:
            mail.logout()
            return None

        raw_response = str(fetch_info).lower()

        # Sender Filter
        if TRUSTED_SENDERS:
            sender_matched = any(sender in raw_response for sender in TRUSTED_SENDERS)
            if not sender_matched:
                print(f"[{time.strftime('%H:%M:%S')}] UID {u_id} skipped: Sender not in TRUSTED_SENDERS.", flush=True)
                mail.logout()
                return None

        # Parse part numbers for image attachments from BODYSTRUCTURE
        # Typical structure returned: ("IMAGE" "JPEG" ... ) or part identifiers like 2, 2.1
        bs_str = str(fetch_info[0]) if fetch_info and len(fetch_info) > 0 else ""
        part_matches = re.findall(r'\(("IMAGE"|"APPLICATION")\s+"(JPEG|PNG|WEBP|HEIC|JPG)"', bs_str, re.IGNORECASE)

        if not part_matches:
            # Fallback: check if the whole email is a single raw image
            has_image = any(ext in raw_response for ext in ['.jpg', '.jpeg', '.png', '.webp', 'image/'])
            if not has_image:
                print(f"[{time.strftime('%H:%M:%S')}] UID {u_id} skipped: No image found.", flush=True)
                mail.logout()
                return None
            target_parts = ['1']
        else:
            # Extract section numbers (e.g. BODY[2] or BODY[2.1])
            sections = re.findall(r'(\d+(?:\.\d+)*)\s+\("IMAGE"', bs_str, re.IGNORECASE)
            target_parts = sections if sections else ['2', '1.2', '1']

        # 2. Fetch raw image bytes directly for target sections
        for section in target_parts:
            status, img_data = mail.uid('fetch', u_id, f'(BODY.PEEK[{section}])')
            if status == 'OK' and img_data and isinstance(img_data[0], tuple):
                raw_bytes = img_data[0][1]
                if raw_bytes and len(raw_bytes) > 100:
                    try:
                        pil_image = Image.open(io.BytesIO(raw_bytes))
                        pil_image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                        attachments_in_msg.append({
                            "image_object": pil_image,
                            "original_name": f"receipt_{u_id}_{section}.jpg",
                        })
                        break # Successfully got primary attachment
                    except Exception:
                        continue

        mail.logout()

        if attachments_in_msg:
            return {"u_id": u_id, "attachments": attachments_in_msg}

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Attachment fetch error for UID {u_id}: {e}", flush=True)

    return None

def download_new_receipts():
    """Fetches unseen emails and streams image attachments in parallel."""
    import imaplib

    print(f"[{time.strftime('%H:%M:%S')}] Connecting to IMAP server...", flush=True)
    saved_in_memory_images = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        
        since_date = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        status, data = mail.uid('search', None, f'(UNSEEN SINCE "{since_date}")')
        
        if status != 'OK' or not data or not data[0]:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass
            return None, []

        email_uids = data[0].decode('utf-8').split()
        print(f"[{time.strftime('%H:%M:%S')}] Found {len(email_uids)} unseen email(s). Downloading attachments in parallel...", flush=True)

        if email_uids:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(email_uids), 5)) as executor:
                results = executor.map(fetch_attachment_for_uid, email_uids)
                for res in results:
                    if res:
                        saved_in_memory_images.append(res)

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
# THREAD-ISOLATED VISION ENGINE (DIRECT REST API)
# =====================================================================
def analyze_image_with_gemini(img_obj, assigned_key, max_fast_retries=1):
    """Processes image directly over REST to bypass Python SDK hangs."""
    buffer = io.BytesIO()
    img_obj.save(buffer, format="JPEG", quality=85)
    img_bytes = buffer.getvalue()
    base64_image = base64.b64encode(img_bytes).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={assigned_key}"

    prompt = (
        "Analyze this receipt image and extract data into a strict JSON layout.\n"
        "1. Identify the store name as 'vendor'.\n"
        "2. Find the final mathematical grand total amount as 'total' (no currency symbols).\n"
        "3. Categorize the transaction into 'category' matching exactly: 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'.\n"
        "4. Identify the transaction or purchase date printed on the receipt as 'date' (format as YYYY-MM-DD if clear, otherwise extract text string).\n"
        "5. Extract all purchased individual items as an array in 'items'. For each item include:\n"
        "   - 'name': item title or description\n"
        "   - 'price': item cost/price (no currency symbols, or empty string if not shown)\n"
        "   - 'weight': item weight or quantity by weight (e.g., '50 lbs', '2.5 kg', or empty string if not specified)"
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
        
        if data is None:
            continue
            
        vendor = re.sub(r'[\\/*?:"<>|]', "", data.get('vendor', 'Unknown_Vendor'))[:20].strip()
        category = data.get('category', 'Farm:General')
        total = data.get('total', '[Amount Not Found]')
        receipt_date = data.get('date', '[Date Not Found]')
        items_list = data.get('items', [])
        
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
        
        new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}___{filename}"
        
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
