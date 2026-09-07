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

# Dynamic API Key Rotation Pool Setup
GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3")
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

current_key_index = 0

def get_next_client():
    """Cycles seamlessly to the next available free API key to drop unneeded 60s sleep delays."""
    global current_key_index
    if not GEMINI_KEYS:
        raise ValueError("Critical Error: No valid Gemini API keys found inside environmental configurations.")
    
    selected_key = GEMINI_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
    
    print(f"🔄 Rotating credentials... Swapping execution context to API Key Slot #{current_key_index + 1}")
    return genai.Client(api_key=selected_key)

# Dynamic Trusted Senders List Setup (No hardcoded values)
TRUSTED_SENDERS_RAW = os.getenv("TRUSTED_SENDERS", "")
TRUSTED_SENDERS = [email.strip() for email in TRUSTED_SENDERS_RAW.split(",") if email.strip()]

def setup_folders():
    # Returns current root directory to keep GitHub saves error-free
    return "."

# =====================================================================
# HIGH-SPEED INBOX SWEEPER (IN-MEMORY STREAMS WITH ANTI-SPAM FILTER)
# =====================================================================
def download_new_receipts():
    """Fetches all unread emails and matches them against trusted senders instantly in memory."""
    saved_in_memory_images = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")  # Correctly sweeps your active incoming inbox instantly
        
        # Pull ALL unread message IDs instantly in a clean string format
        status, data = mail.uid('search', None, 'UNSEEN')
        email_uids = []
        
        if status == 'OK' and data:
            for item in data:
                if isinstance(item, bytes):
                    email_uids.extend(item.decode('utf-8').split())
        
        for u_id in email_uids:
            # Fetch the sender header details first to check authorization
            status, header_data = mail.uid('fetch', u_id, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
            if status != 'OK' or not header_data:
                continue
                
            # Safely unpack raw sender text from the nested list-tuple layout
            try:
                if isinstance(header_data, list) and len(header_data) > 0:
                    raw_header_bytes = header_data[0][1]
                else:
                    raw_header_bytes = b""
                header_text = raw_header_bytes.decode('utf-8', errors='ignore').lower()
            except (IndexError, TypeError, AttributeError):
                header_text = ""
            
            # ANTI-SPAM PROTECTION: Skip the message immediately if it doesn't match your trusted pool
            if TRUSTED_SENDERS:
                if not any(sender.lower() in header_text for sender in TRUSTED_SENDERS):
                    continue
            
            # Confirmed trusted sender -> Fetch full email bytes safely
            status, fetch_data = mail.uid('fetch', u_id, '(BODY.PEEK[])')
            if status != 'OK' or not fetch_data:
                continue
                
            # Safely extract and unpack the raw email bytes from the list container instantly
            try:
                if isinstance(fetch_data, list) and len(fetch_data) > 0:
                    raw_bytes = fetch_data[0][1]
                else:
                    raw_bytes = b""
                    
                if isinstance(raw_bytes, bytes) and len(raw_bytes) > 0:
                    msg = email.message_from_bytes(raw_bytes)
                else:
                    continue
            except (IndexError, TypeError):
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
# ROTATING CLOUD VISION ENGINE WITH RATE LIMIT BYPASS FAILSAFES
# =====================================================================
def analyze_image_with_gemini(img_obj):
    """Creates a thread-isolated connection session to completely bypass internal SDK queue bottlenecks."""
    max_retries = len(GEMINI_KEYS) * 2
    
    # OPTIMIZATION: Pull a fresh key from the rotation pool instantly for this specific thread
    global current_key_index
    if not GEMINI_KEYS:
        raise ValueError("Critical Error: No valid Gemini API keys found inside environmental configurations.")
    
    selected_key = GEMINI_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
    
    # Initialize a completely independent thread-local client context session
    local_client = genai.Client(api_key=selected_key)
    print(f"🚀 Thread spawned clean network connection using API Key Slot #{current_key_index + 1}")
    
    temperatures = [0.0, 0.3, 0.7, 1.0, 1.0, 1.0]
    
    for attempt in range(max_retries):
        try:
            current_temp = temperatures[attempt] if attempt < len(temperatures) else 1.0
            
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
                    temperature=current_temp,
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
            
            extracted_json = json.loads(response.text.strip())
            
            if extracted_json.get("date") in ["[Date Not Found]", "", "None", "Unknown"]:
                if attempt < max_retries - 1:
                    raise ValueError("Target field missing in visual extraction output payload.")
            
            return extracted_json
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                # Key rotation failsafe: Grab a fresh key instantly and spawn a brand new client context
                selected_key = GEMINI_KEYS[current_key_index]
                current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
                print(f"⚠️ Worker rate-limited. Instantly switching thread session to API Key Slot #{current_key_index + 1}")
                local_client = genai.Client(api_key=selected_key)
            elif "503" in error_msg or "UNAVAILABLE" in error_msg:
                time.sleep(2)
            else:
                if "Target field missing" in error_msg and attempt < max_retries - 1:
                    continue
                print(f"Direct analysis error: {error_msg}")
                break
                
    return {"vendor": "Unknown_Vendor", "total": "[Amount Not Found]", "category": "Farm:General", "date": "[Date Not Found]", "items": ["Error: Key rotation pool fully exhausted."]}

# =====================================================================
# PARALLEL WORKER ENGINE (RAM STREAM INJECTION)
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
    receipt_date = data.get('date', '[Date Not Found]') # Added variable capture
    items_list = data.get('items', [])
    
    if total and not str(total).startswith('$'):
        total = f"${total}"
    
    formatted_items = ""
    for item in items_list:
        formatted_items += f"  - {item}\n"
    if not formatted_items:
        formatted_items = "  - [No Items Found]\n"
    
    new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}___{filename}"
    
    # Integrated receipt_date directly into the text layout block output structure
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
# BATCH EXECUTION MAIN PIPELINE (CONCURRENT BALANCER)
# =====================================================================
def process_receipts(receipt_memory_list, processed_dir):
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    log_blocks_gathered = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(process_single_memory_receipt, rec): rec for rec in receipt_memory_list}
        
        for future in concurrent.futures.as_completed(futures):
            try:
                result_block = future.result()
                log_blocks_gathered.append(result_block)
            except Exception as e:
                failed_item = futures[future]
                print(f"Thread worker critical exception on in-memory item {failed_item['original_name']}: {e}")

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
