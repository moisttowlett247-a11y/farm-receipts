import imaplib
import email
import os
import re
import json
from datetime import datetime

# =====================================================================
# CONFIGURATION
# =====================================================================
EMAIL_USER = os.getenv("EMAIL_USER", "your_local_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_local_config_pass")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IMAP_SERVER = "imap.gmail.com"

# Import the official Google GenAI Library tools
try:
    from google import genai
    from google.genai import types
except ImportError:
    pass

def get_current_date_str():
    return datetime.now().strftime("%Y-%m-%d")

def setup_folders():
    today = get_current_date_str()
    download_path = os.path.join("./Receipts_Downloaded", today)
    processed_path = os.path.join("./Receipts_Processed", today)
    os.makedirs(download_path, exist_ok=True)
    os.makedirs(processed_path, exist_ok=True)
    return download_path, processed_path

# =====================================================================
# EMAIL HARVESTER
# =====================================================================
def download_new_receipts(download_dir):
    saved_files = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select('"[Gmail]/All Mail"')
        
        status, data = mail.search(None, '(UNSEEN)')
        email_ids = []
        
        if status == 'OK' and data and isinstance(data, list):
            raw_data = data[0]
            if isinstance(raw_data, bytes):
                email_ids = raw_data.decode('utf-8').split()
        
        for e_id in email_ids:
            status, fetch_data = mail.fetch(e_id, '(RFC822)')
            if status != 'OK' or not fetch_data:
                continue
                
            raw_email = fetch_data[0][1]
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
                    filepath = os.path.join(download_dir, filename)
                    if os.path.exists(filepath):
                        filepath = os.path.join(download_dir, f"{datetime.now().strftime('%H%M%S_')}{filename}")
                        
                    with open(filepath, 'wb') as f:
                        f.write(part.get_payload(decode=True))
                    saved_files.append(filepath)
                    has_valid_attachments = True
            
            if has_valid_attachments:
                mail.store(e_id, '+FLAGS', '\\Seen')
                try:
                    mail.create("Processed_Receipts")
                    mail.copy(e_id, "Processed_Receipts")
                    mail.store(e_id, '+FLAGS', '\\Deleted')
                except:
                    pass
                    
        mail.expunge()
        mail.logout()
    except Exception as e:
        print(f"Inbox processing warning/error: {e}")
    return saved_files

# =====================================================================
# OFFICIAL GOOGLE GENAI CLOUD VISION ENGINE
# =====================================================================
def analyze_image_with_gemini(file_path):
    """Leverages official Google SDK tunnels to parse structured receipts safely."""
    try:
        from PIL import Image
        img = Image.open(file_path)
        
        # Initialize official GenAI client using vault variables
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = (
            "Analyze this receipt image and extract data into a strict JSON layout.\n"
            "Identify the store name as 'vendor'.\n"
            "Find the final mathematical grand total amount as 'total' (do not include currency symbols).\n"
            "Categorize the transaction into 'category' matching exactly: 'Farm:Cows', 'Farm:Chickens', or 'Farm:General'."
        )
        
        # Call official content generation endpoint using explicit JSON configuration schemas
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[img, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "vendor": types.Schema(type=types.Type.STRING),
                        "total": types.Schema(type=types.Type.STRING),
                        "category": types.Schema(type=types.Type.STRING),
                    },
                    required=["vendor", "total", "category"],
                ),
            ),
        )
        
        return json.loads(response.text.strip())
        
    except Exception as e:
        print(f"Cloud analysis error fallback triggered: {e}")
        return {"vendor": "Unknown_Vendor", "total": "[Amount Not Found]", "category": "Farm:General"}

# =====================================================================
# BATCH EXECUTION MAIN PIPELINE
# =====================================================================
def process_receipts(downloaded_files, processed_dir):
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    
    with open(log_file_path, "a", encoding="utf-8") as log:
        log.write(f"\n==================================================\n")
        log.write(f"BATCH RUN DATE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"==================================================\n")
        
        for file_path in downloaded_files:
            filename = os.path.basename(file_path)
            print(f"Offloading cloud analysis for: {filename}...")
            
            data = analyze_image_with_gemini(file_path)
            
            vendor = re.sub(r'[\\/*?:"<>|]', "", data.get('vendor', 'Unknown_Vendor'))[:20].strip()
            category = data.get('category', 'Farm:General')
            total = data.get('total', '[Amount Not Found]')
            
            if total and not str(total).startswith('$'):
                total = f"${total}"
            
            new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}___{filename}"
            final_processed_path = os.path.join(processed_dir, new_filename)
            
            os.rename(file_path, final_processed_path)
            
            log_block = (
                f"File Name: {new_filename}\n"
                f"Category:  {category}\n"
                f"Vendor:    {vendor}\n"
                f"Amount:    {total}\n"
                f"--------------------------------------------------\n"
            )
            log.write(log_block)
            print(f"Completed: {new_filename}")

if __name__ == "__main__":
    print("Free Farm Receipt Processor System Initialized.")
    download_folder, processed_folder = setup_folders()
    new_paths = download_new_receipts(download_folder)
    
    if new_paths:
        process_receipts(new_paths, processed_folder)
    else:
        print("Inbox check clear. No unread receipt attachments detected.")
