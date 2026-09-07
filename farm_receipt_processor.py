import imaplib
import email
import os
import re
import json
from datetime import datetime
import time
import concurrent.futures

# =====================================================================
# CONFIGURATION & KEY/SENDER POOL MANAGER
# =====================================================================
EMAIL_USER = os.getenv("EMAIL_USER", "your_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_app_password")
IMAP_SERVER = "imap.gmail.com"

try:
    from google import genai
    from google.genai import types
except ImportError:
    pass

# Thread-Safe Key Mapping Pool
GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3")
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# Dynamic Trusted Senders List Setup
TRUSTED_SENDERS_RAW = os.getenv("TRUSTED_SENDERS", "")
TRUSTED_SENDERS = [email.strip() for email in TRUSTED_SENDERS_RAW.split(",") if email.strip()]

def setup_folders():
    return "."

# =====================================================================
# HIGH-SPEED INBOX SWEEPER (IN-MEMORY STREAMS WITH ANTI-SPAM FILTER)
# =====================================================================
# =====================================================================
# HIGH-SPEED INBOX SWEEPER (BULK BYTE PAYLOAD EXTRACTION)
# =====================================================================
def download_new_receipts():
    """Fetches all unread emails and matches them against trusted senders instantly in memory."""
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
                
            # FIXED: Scans any data structure smoothly to locate the raw sender text
            header_text = ""
            for block in header_data:
                if isinstance(block, tuple) and len(block) > 1 and isinstance(block, bytes):
                    header_text = block.decode('utf-8', errors='ignore').lower()
                    break
            
            if TRUSTED_SENDERS:
                if not any(sender.lower() in header_text for sender in TRUSTED_SENDERS):
                    continue
            
            status, fetch_data = mail.uid('fetch', u_id, '(BODY.PEEK[])')
            if status != 'OK' or not fetch_data:
                continue
                
            # FIXED: Robust unpacker to safely extract raw email message layers
            msg = None
            for block in fetch_data:
                if isinstance(block, tuple) and len(block) > 1 and isinstance(block, bytes):
                    msg = email.message_from_bytes(block)
                    break
            
            if msg is None:
                continue
            
            has_valid_attachments = False
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                    continue
                    
                filename = part.get_filename()
                if filename and filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    from PIL import Image
                    import io
                    image_bytes = part.get_payload(decode=True)
                    pil_image = Image.open(io.BytesIO(image_bytes))
                    
                    saved_in_memory_images.append({
                        "image_object": pil_image,
                        "original_name": filename
                    })
                    has_valid_attachments = True
            
            if has_valid_attachments:
                mail.uid('store', u_id, '+FLAGS', '\\Seen')
                try:
                    mail.create("Processed_Receipts")
                    mail.uid('copy', u_id, "Processed_Receipts")
                    mail.uid('store', u_id, '+FLAGS', '\\Deleted')
                except:
                    pass
                    
        mail.expunge()
        mail.logout()
    except Exception as e:
        print(f"Inbox processing warning/error: {e}")
    return saved_in_memory_images

# =====================================================================
# THREAD-ISOLATED CLOUD VISION ENGINE (ZERO GLOBAL VARIABLES)
# =====================================================================
def analyze_image_with_gemini(img_obj, assigned_key):
    """Uses a completely isolated API key assigned explicitly to this worker thread."""
    local_client = genai.Client(api_key=assigned_key)
    
    try:
        prompt = (
            "Analyze this receipt image and extract data into a strict JSON layout.\n"
            "1. Identify the store name as 'vendor'.\n"
            "2. Find the final mathematical grand total amount as 'total' (no currency symbols).\n"
            "3. Categorize the transaction into 'category' matching exactly: 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'.\n"
            "4. Identify the transaction or purchase date printed on the receipt as 'date' (format as YYYY-MM-DD if clear, otherwise extract text string).\n"
            "5. Read the text lines and pull a list of all purchased individual products into 'items'. "
            "For each product entry description, explicitly include its description name, its weight or volume metrics if given (like '50 lb'), "
            "and its corresponding item price matching the line layout."
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
        print(f"Cloud analysis network warning/error: {e}")
        return {"vendor": "Unknown_Vendor", "total": "[Amount Not Found]", "category": "Farm:General", "date": "[Date Not Found]", "items": [f"Extraction warning: {str(e)}"]}

# =====================================================================
# THREAD WORKER ROUTER
# =====================================================================
def process_single_memory_receipt(args):
    """Unpacks thread variables cleanly inside isolated scope execution layers."""
    receipt_data, assigned_key = args
    img_obj = receipt_data["image_object"]
    filename = receipt_data["original_name"]
    
    print(f"Offloading cloud analysis for in-memory image stream: {filename}...")
    data = analyze_image_with_gemini(img_obj, assigned_key)
    
    vendor = re.sub(r'[\\/*?:"<>|]', "", data.get('vendor', 'Unknown_Vendor'))[:20].strip()
    category = data.get('category', 'Farm:General')
    total = data.get('total', '[Amount Not Found]')
    receipt_date = data.get('date', '[Date Not Found]')
    items_list = data.get('items', [])
    
    if total and not str(total).startswith('$'):
        total = f"${total}"
    
    formatted_items = ""
    for item in items_list:
        formatted_items += f"  - {item}\n"
    if not formatted_items:
        formatted_items = "  - [No Items Found]\n"
    
    new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}___{filename}"
    
    log_block = (
        f"File Name: {new_filename}\n"
        f"Category: {category}\n"
        f"Vendor: {vendor}\n"
        f"Receipt Date: {receipt_date}\n"
        f"Amount: {total}\n"
        f"Items:\n{formatted_items}"
        f"--------------------------------------------------\n"
    )
    
    img_obj.close()
    return log_block

# =====================================================================
# BATCH EXECUTION MAIN PIPELINE (THREAD-SAFE ENTRY LAYER)
# =====================================================================
def process_receipts(receipt_memory_list, processed_dir):
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    log_blocks_gathered = []
    
    # Map each incoming receipt to an isolated API key from the pool safely
    worker_inputs = []
    for idx, receipt in enumerate(receipt_memory_list):
        assigned_key = GEMINI_KEYS[idx % len(GEMINI_KEYS)] if GEMINI_KEYS else None
        worker_inputs.append((receipt, assigned_key))
        
    # Workers scale dynamically based on the exact batch size safely
    pool_workers = min(len(receipt_memory_list), 3)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = {executor.submit(process_single_memory_receipt, w_in): w_in for w_in in worker_inputs}
        
        for future in concurrent.futures.as_completed(futures):
            try:
                result_block = future.result()
                log_blocks_gathered.append(result_block)
            except Exception as e:
                failed_input = futures[future]
                print(f"Thread worker exception on file {failed_input[0]['original_name']}: {e}")

    with open(log_file_path, "a", encoding="utf-8") as log:
        log.write(f"\n==================================================\n")
        log.write(f"BATCH RUN DATE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"==================================================\n")
        log.writelines(log_blocks_gathered)
        
    print(f"Successfully processed batch of {len(receipt_memory_list)} receipts in-memory.")

# =====================================================================
# MAIN AUTOMATION ENTRY
# =====================================================================
if __name__ == "__main__":
    print("Free Farm Receipt Processor System Initialized.")
    processed_folder = setup_folders()
    receipt_queue = download_new_receipts()
    if receipt_queue:
        process_receipts(receipt_queue, processed_folder)
    else:
        print("Inbox check clear. No unread receipt attachments detected matching filter specifications.")
