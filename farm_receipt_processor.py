import imaplib
import email
import os
import re
import json
from datetime import datetime
import time
import concurrent.futures

# =====================================================================
# CONFIGURATION & KEY MANAGER
# =====================================================================
EMAIL_USER = os.getenv("EMAIL_USER", "your_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_app_password")
IMAP_SERVER = "imap.gmail.com"

try:
    from google import genai
    from google.genai import types
except ImportError:
    pass

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

TRUSTED_SENDERS_RAW = os.getenv("TRUSTED_SENDERS", "")
TRUSTED_SENDERS = [email.strip() for email in TRUSTED_SENDERS_RAW.split(",") if email.strip()]

def setup_folders():
    return "."

# =====================================================================
# HIGH-SPEED INBOX SWEEPER
# =====================================================================
def download_new_receipts():
    """Fetches unread emails from your trusted list in memory."""
    saved_in_memory_images = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        
        status, data = mail.uid('search', None, 'UNSEEN')
        email_uids = []
        if status == 'OK' and data:
            for item in data:
                if isinstance(item, bytes):
                    email_uids.extend(item.decode('utf-8').split())
                    
        for u_id in email_uids:
            status, header_data = mail.uid('fetch', u_id, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
            if status != 'OK' or not header_data:
                continue
            
            header_text = ""
            try:
                for block in header_data:
                    if isinstance(block, tuple) and len(block) > 1:
                        header_text = block[1].decode('utf-8', errors='ignore').lower()
                        break
            except Exception:
                header_text = ""
                
            if TRUSTED_SENDERS and not any(sender.lower() in header_text for sender in TRUSTED_SENDERS):
                continue
                
            status, fetch_data = mail.uid('fetch', u_id, '(BODY.PEEK[])')
            if status != 'OK' or not fetch_data:
                continue
                
            msg = None
            try:
                for block in fetch_data:
                    if isinstance(block, tuple) and len(block) > 1:
                        msg = email.message_from_bytes(block[1])
                        break
            except Exception:
                msg = None
                
            if msg is None:
                continue
                
            has_valid_attachments = False
            attachments_in_msg = []
            
            root_content_type = msg.get_content_type().lower()
            if root_content_type in ['image/jpeg', 'image/png', 'image/jpg']:
                from PIL import Image
                import io
                image_bytes = msg.get_payload(decode=True)
                pil_image = Image.open(io.BytesIO(image_bytes))
                attachments_in_msg.append({
                    "image_object": pil_image,
                    "original_name": f"direct_upload_{u_id}.jpg",
                })
                has_valid_attachments = True
            else:
                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                        continue
                    filename = part.get_filename()
                    if filename and filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                        from PIL import Image
                        import io
                        image_bytes = part.get_payload(decode=True)
                        pil_image = Image.open(io.BytesIO(image_bytes))
                        attachments_in_msg.append({
                            "image_object": pil_image,
                            "original_name": filename,
                        })
                        has_valid_attachments = True
                        
            if has_valid_attachments:
                saved_in_memory_images.append({
                    "u_id": u_id,
                    "attachments": attachments_in_msg,
                })
                
        if saved_in_memory_images:
            return mail, saved_in_memory_images
        else:
            mail.logout()
    except Exception as e:
        print(f"Inbox processing warning/error: {e}")
    return None, []

# =====================================================================
# THREAD-ISOLATED VISION ENGINE (NO RE-TRY DELAY HANGS)
# =====================================================================
def analyze_image_with_gemini(img_obj, assigned_key):
    local_client = genai.Client(api_key=assigned_key)
    try:
        prompt = (
            "Analyze this receipt image and extract data into a strict JSON layout.\n"
            "1. Identify the store name as 'vendor'.\n"
            "2. Find the final mathematical grand total amount as 'total' (no currency symbols).\n"
            "3. Categorize the transaction into 'category' matching exactly: 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'.\n"
            "4. Identify the transaction or purchase date printed on the receipt as 'date' (format as YYYY-MM-DD if clear, otherwise extract text string).\n"
            "5. Read the text lines and pull a list of all purchased individual products into 'items'."
        )
        
        response = local_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[img_obj, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "vendor": types.Schema(type=types.Type.STRING),
                        "total": types.Schema(type=types.Type.STRING),
                        "category": types.Schema(type=types.Type.STRING),
                        "date": types.Schema(type=types.Type.STRING),
                        "items": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING)
                        ),
                    },
                    required=["vendor", "total", "category", "date", "items"],
                ),
            ),
        )
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"Cloud server drop (Skipping write layout block): {e}")
        return None

# =====================================================================
# INDEPENDENT WORKER SCOPE ROUTER
# =====================================================================
def process_single_email_group(args):
    email_package, assigned_key = args
    attachments = email_package["attachments"]
    u_id = email_package["u_id"]
    gathered_log_blocks = []
    
    for attachment in attachments:
        img_obj = attachment["image_object"]
        filename = attachment["original_name"]
        print(f"Offloading cloud analysis for: {filename}...")
        
        data = analyze_image_with_gemini(img_obj, assigned_key)
        img_obj.close()
        
        if data is None:
            print(f"⚠️ Failed to process image '{filename}' on UID {u_id}. Skipping attachment...")
            continue
            
        vendor = re.sub(r'[\\/*?:"<>|]', "", data.get('vendor', 'Unknown_Vendor'))[:20].strip()
        category = data.get('category', 'Farm:General')
        total = data.get('total', '[Amount Not Found]')
        receipt_date = data.get('date', '[Date Not Found]')
        items_list = data.get('items', [])
        
        if total and not str(total).startswith('$'):
            total = f"${total}"
            
        formatted_items = (
            "".join([f"  - {item}\n" for item in items_list])
            if items_list else "  - [No Items Found]\n"
        )
        
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
# PIPELINE COORDINATOR (WITH CHRONOLOGICAL DATE SORTING)
# =====================================================================
def process_receipts(mail_session, email_packages, processed_dir):
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    extracted_records = []
    worker_inputs = []
    
    for idx, package in enumerate(email_packages):
        assigned_key = GEMINI_KEYS[idx % len(GEMINI_KEYS)] if GEMINI_KEYS else None
        worker_inputs.append((package, assigned_key))
        
    pool_workers = min(len(email_packages), 3)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = {executor.submit(process_single_email_group, w_in): w_in for w_in in worker_inputs}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                u_id = result["u_id"]
                if result["success"]:
                    extracted_records.extend(result["blocks"])
                    mail_session.uid('store', u_id, '+FLAGS', '\\Seen')
                    try:
                        mail_session.create("Processed_Receipts")
                        mail_session.uid('copy', u_id, "Processed_Receipts")
                        mail_session.uid('store', u_id, '+FLAGS', '\\Deleted')
                    except:
                        pass
                else:
                    print(f"⚠️ 503 Server Error / total failure caught on UID {u_id}. Keeping email UNREAD for next safety run.")
            except Exception as e:
                print(f"Thread processor critical error: {e}")
                
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
            
        print(f"Successfully sorted and processed {len(extracted_records)} records chronologically to your ledger.")
        
    mail_session.expunge()
    mail_session.logout()

# =====================================================================
# MAIN ENTRY
# =====================================================================
if __name__ == "__main__":
    print("Free Farm Receipt Processor System Initialized.")
    processed_folder = setup_folders()
    mail_session, email_queue = download_new_receipts()
    if email_queue:
        process_receipts(mail_session, email_queue, processed_folder)
    else:
        print("Inbox check clear. No unread receipts found matching filter rules.")
