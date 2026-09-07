import imaplib
import email
import os
import re
import json
from datetime import datetime
import http.client

# =====================================================================
# CONFIGURATION
# =====================================================================
EMAIL_USER = os.getenv("EMAIL_USER", "your_local_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_local_config_pass")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IMAP_SERVER = "imap.gmail.com"

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
# INDESTRUCTIBLE EMAIL HARVESTER
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
# HIGH-SPEED FREE GOOGLE CLOUD VISION ENGINE
# =====================================================================
def analyze_image_with_gemini(file_path):
    """Leverages Google's cloud server to parse totals and categories in milliseconds."""
    try:
        with open(file_path, "rb") as image_file:
            import base64
            image_data = base64.b64encode(image_file.read()).decode("utf-8")
            
        # Determine image format type
        mime_type = "image/jpeg" if file_path.lower().endswith(('.jpg', '.jpeg')) else "image/png"
        
        # Craft a precise visual directive request
        prompt = (
            "Analyze this receipt image. Even if there are dark shadows or boxed lines, look for the final mathematical Grand Total. "
            "Extract the accurate vendor name (the company title at the top). "
            "Categorize the transaction into one of these three exact subcategories: "
            "1. 'Farm:Cows' (for cattle feed, ear tags, vet care, mineral blocks, etc.) "
            "2. 'Farm:Chickens' (for poultry scratch, wire netting, coops, heat lamps, etc.) "
            "3. 'Farm:General' (for compressors, tools, hardware, fuel, items matching neither animal). "
            "Provide the answer strictly as a clean JSON layout block with keys 'vendor', 'total', and 'category'. Do not include markdown code block styling ticks."
        )
        
        # Build raw request payload
        payload = json.dumps({
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": image_data}}
                ]
            }]
        })
        
        # Execute direct low-level API call to avoid importing heavy third-party SDK libraries
        conn = http.client.HTTPSConnection("://googleapis.com")
        headers = {'Content-Type': 'application/json'}
        conn.request("POST", f"/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", payload, headers)
        
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        conn.close()
        
        # Unpack result strings cleanly
        result_json = json.loads(data)
        text_response = result_json['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # Clean potential markdown layout wrappers if present
        text_response = text_response.replace("```json", "").replace("```", "").strip()
        return json.loads(text_response)
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
            
            # Send file token directly to Google's backend engine
            data = analyze_image_with_gemini(file_path)
            
            vendor = re.sub(r'[\\/*?:"<>|]', "", data.get('vendor', 'Unknown_Vendor'))[:20]
            category = data.get('category', 'Farm:General')
            total = data.get('total', '[Amount Not Found]')
            
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
