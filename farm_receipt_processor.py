import imaplib
import email
import os
import re
import json
from datetime import datetime
import time
import concurrent.futures

# =====================================================================
# CONFIGURATION
# =====================================================================
EMAIL_USER = os.getenv("EMAIL_USER", "your_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_app_password")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IMAP_SERVER = "imap.gmail.com"

try:
    from google import genai
    from google.genai import types
except ImportError:
    pass

# Initialize client globally once to eliminate connection overhead inside threads
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    client = None

def get_current_date_str():
    return datetime.now().strftime("%Y-%m-%d")

def setup_folders():
    today = get_current_date_str()
    processed_path = os.path.join("./Receipts_Processed", today)
    os.makedirs(processed_path, exist_ok=True)
    return processed_path

# =====================================================================
# BULK EMAIL INBOX SWEEPER
# =====================================================================
def download_new_receipts():
    """Fetches all unread emails and extracts image payloads entirely in memory."""
    saved_in_memory_images = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select('"[Gmail]/All Mail"')
        
        status, data = mail.uid('search', None, '(UNSEEN)')
        email_uids = []
        
        if status == 'OK' and data:
            for item in data:
                if isinstance(item, bytes):
                    email_uids.extend(item.decode('utf-8').split())
        
        for u_id in email_uids:
            # Pull full data to extract multipart groups seamlessly
            status, fetch_data = mail.uid('fetch', u_id, '(BODY.PEEK[])')
            if status != 'OK' or not fetch_data:
                continue
                
            raw_email = fetch_data[0][1] if isinstance(fetch_data, list) and len(fetch_data) > 0 else fetch_data
            if isinstance(raw_email, bytes):
                msg = email.message_from_bytes(raw_email)
            else:
                continue
            
            has_valid_attachments = False
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                    continue
                    
                filename = part.get_filename()
                if filename and filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    # OPTIMIZATION: Read raw payload directly into RAM buffer as a PIL image target
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
# SYSTEM-FORCED CLOUD VISION ENGINE WITH RETRY FAILSAFE LOGIC
# =====================================================================
def analyze_image_with_gemini(img_obj):
    """Leverages Google's cloud server with retry handling for 503 and 429 errors."""
    max_retries = 3
    retry_delay = 5  # Base cooling delay for handling quick rate-limit bursts
    
    for attempt in range(max_retries):
        try:
            prompt = (
                "Analyze this receipt image and extract data into a strict JSON layout.\n"
                "1. Identify the store name as 'vendor'.\n"
                "2. Find the final mathematical grand total amount as 'total' (no currency symbols).\n"
                "3. Categorize the transaction into 'category' matching exactly: 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'.\n"
                "4. Read the text lines and pull a list of all purchased individual products into 'items'. "
                "For each product entry description, explicitly include its description name, its weight or volume metrics if given (like '50 lb'), "
                "and its corresponding item price matching the line layout."
            )
            
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=[img_obj, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "vendor": types.Schema(type=types.Type.STRING),
                            "total": types.Schema(type=types.Type.STRING),
                            "category": types.Schema(type=types.Type.STRING),
                            "items": types.Schema(
                                type=types.Type.ARRAY,
                                items=types.Schema(type=types.Type.STRING)
                            ),
                        },
                        required=["vendor", "total", "category", "items"],
                    ),
                ),
            )
            
            return json.loads(response.text.strip())
            
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                current_delay = retry_delay * (attempt + 1)
                print(f"Rate limited or server busy. Retrying attempt {attempt + 1}/{max_retries} in {current_delay}s...")
                time.sleep(current_delay)
            else:
                print(f"Direct analysis error: {error_msg}")
                break
                
    return {"vendor": "Unknown_Vendor", "total": "[Amount Not Found]", "category": "Farm:General", "items": ["Error: Cloud traffic spike or quota cap hit. Please run workflow again later."]}

# =====================================================================
# PARALLEL WORKER ENGINE (IN-MEMORY EXECUTION)
# =====================================================================
def process_single_memory_receipt(receipt_data):
    """Processes an image directly from RAM buffer without hard drive read/write cycles."""
    img_obj = receipt_data["image_object"]
    filename = receipt_data["original_name"]
    
    print(f"Offloading cloud analysis for in-memory image stream: {filename}...")
    data = analyze_image_with_gemini(img_obj)
    
    vendor = re.sub(r'[\\/*?:"<>|]', "", data.get('vendor', 'Unknown_Vendor'))[:20].strip()
    category = data.get('category', 'Farm:General')
    total = data.get('total', '[Amount Not Found]')
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
        f"Amount: {total}\n"
        f"Items:\n{formatted_items}"
        f"--------------------------------------------------\n"
    )
    
    # Explicit garbage collection of image reference object inside thread scope
    img_obj.close()
    return log_block

# =====================================================================
# BATCH EXECUTION MAIN PIPELINE (BALANCED WORKER POOL)
# =====================================================================
def process_receipts(receipt_memory_list, processed_dir):
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    log_blocks_gathered = []
    
    # max_workers=2 keeps requests balanced under the free tier RPM rate thresholds
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(process_single_memory_receipt, rec): rec for rec in receipt_memory_list}
        
        for future in concurrent.futures.as_completed(futures):
            try:
                result_block = future.result()
                log_blocks_gathered.append(result_block)
            except Exception as e:
                failed_item = futures[future]
                print(f"Thread worker critical exception on in-memory item {failed_item['original_name']}: {e}")

    # Open ledger exactly once to eliminate lock starvation and speed up I/O
    with open(log_file_path, "a", encoding="utf-8") as log:
        log.write(f"\n==================================================\n")
        log.write(f"BATCH RUN DATE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"==================================================\n")
        log.writelines(log_blocks_gathered)
        
    print(f"Successfully processed batch of {len(receipt_memory_list)} receipts in-memory.")

# =====================================================================
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
        print("Inbox check clear. No unread receipt attachments detected.")
