import imaplib
import email
import os
import re
from datetime import datetime
from PIL import Image
import cv2
import numpy as np
import pytesseract

# =====================================================================
# CONFIGURATION
# =====================================================================
# WINDOWS USERS: If your system can't find Tesseract, uncomment the line below 
# and update it to the exact path where your Tesseract-OCR was installed:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Securely grab passwords from your GitHub Secret Vault
EMAIL_USER = os.getenv("EMAIL_USER", "your_local_email@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "your_local_config_pass")
IMAP_SERVER = "imap.gmail.com"

# --- SMART CATEGORIZATION DEFINITIONS ---
KEYWORDS_COWS = ['cow', 'cattle', 'calf', 'heifer', 'bull', 'steer', 'bovine', 'vet', 'ear tag', 'sweet feed', 'milking', 'dehorner']
KEYWORDS_CHICKENS = ['chicken', 'chick', 'hen', 'rooster', 'coop', 'poultry', 'scratch', 'egg', 'brooder', 'wire mesh', 'netting']
CATTLE_SCALE_WORDS = ['50 lb', '50lb', 'block', 'bulk', 'pallet', '50-pound', 'mineral block']

VENDORS_CHICKEN_ONLY = ['meyer hatchery', 'mcmurray', 'poultrysupply']
VENDORS_COW_ONLY = ['valley vet', 'cattle store', 'livestock direct']

# =====================================================================
# DIRECTORY & FILE SETUP
# =====================================================================
def get_current_date_str():
    """Returns today's date formatted as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")

def setup_folders():
    """Creates daily folders for organized tracking."""
    today = get_current_date_str()
    download_path = os.path.join("./Receipts_Downloaded", today)
    processed_path = os.path.join("./Receipts_Processed", today)
    
    os.makedirs(download_path, exist_ok=True)
    os.makedirs(processed_path, exist_ok=True)
    return download_path, processed_path

# =====================================================================
# MULTI-RECEIPT PICTURE SPLITTING (OPENCV)
# =====================================================================
def split_multiple_receipts(image_path, download_dir):
    """
    Scans a single photo for multiple receipts. If found, crops them 
    out and saves them separately to prevent mixed OCR text.
    """
    image = cv2.imread(image_path)
    if image is None:
        return [image_path]
        
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    cropped_files = []
    receipt_count = 0
    base_name = os.path.basename(image_path)
    
    for contour in contours:
        # Filter out small background noises; look for paper-sized shapes
        if cv2.contourArea(contour) > 60000:  
            x, y, w, h = cv2.boundingRect(contour)
            
            # Avoid cropping tiny strips or edges
            if w > 100 and h > 100:
                cropped_img = image[y:y+h, x:x+w]
                cropped_filename = f"split_{receipt_count}_{base_name}"
                cropped_path = os.path.join(download_dir, cropped_filename)
                
                cv2.imwrite(cropped_path, cropped_img)
                cropped_files.append(cropped_path)
                receipt_count += 1
                
    # If the system detected multiple distinct items, clean up the original multi-shot
    if len(cropped_files) > 1:
        try:
            os.remove(image_path)
        except OSError:
            pass
        return cropped_files
        
    # Default back to single image tracking if auto-crop wasn't triggered
    return [image_path]

# =====================================================================
# EMAIL HARVESTER & ARCHIVER (IMAP FAILSAFES)
# =====================================================================
def download_new_receipts(download_dir):
    """
    Connects to email, finds unread attachments, handles local name safety, 
    and archives the email so it is never processed a second time.
    """
    saved_files = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select('"[Gmail]/All Mail"')
        
        # FAILSAFE 1: Gather strictly UNREAD messages
        status, messages = mail.search(None, '(UNSEEN)')
        email_ids = messages.split()
        
        for e_id in email_ids:
            status, data = mail.fetch(e_id, '(RFC822)')
            raw_email = data
            msg = email.message_from_bytes(raw_email)
            
            has_valid_attachments = False
            
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                    continue
                    
                filename = part.get_filename()
                if filename and filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    filepath = os.path.join(download_dir, filename)
                    
                    # LOCAL FAILSAFE: Prevent file overwriting if ran multiple times
                    if os.path.exists(filepath):
                        timestamp_prefix = datetime.now().strftime("%H%M%S_")
                        filename = timestamp_prefix + filename
                        filepath = os.path.join(download_dir, filename)
                        
                    with open(filepath, 'wb') as f:
                        f.write(part.get_payload(decode=True))
                        
                    saved_files.append(filepath)
                    has_valid_attachments = True
            
            # FAILSAFE 2: Mark read and Archive immediately on execution success
            if has_valid_attachments:
                mail.store(e_id, '+FLAGS', '\\Seen')
                try:
                    mail.create("Processed_Receipts")
                    mail.copy(e_id, "Processed_Receipts")
                    mail.store(e_id, '+FLAGS', '\\Deleted')
                except:
                    pass # Fallback if email provider profile blocks folder adjustments
                    
        mail.expunge()
        mail.logout()
    except Exception as e:
        print(f"Inbox processing warning/error: {e}")
        
    return saved_files

# =====================================================================
# INTELLIGENT RULE INTERPRETER (CONTEXT SEARCH)
# =====================================================================
def determine_subcategory(text):
    """
    Context-aware animal matching. Infers ambiguous line items 
    (like salt/vinegar) through wholesale vendor types or shopping card hints.
    """
    text_lower = text.lower()
    
    # LAYER 1: Vendor Type Checks
    if any(vendor in text_lower for vendor in VENDORS_CHICKEN_ONLY):
        return "Farm:Chickens"
    if any(vendor in text_lower for vendor in VENDORS_COW_ONLY):
        return "Farm:Cows"
        
    # LAYER 2: Shopping Basket Companion Items Check
    has_chicken_clues = any(kw in text_lower for kw in KEYWORDS_CHICKENS)
    has_cow_clues = any(kw in text_lower for kw in KEYWORDS_COWS)
    
    if has_chicken_clues and not has_cow_clues:
        return "Farm:Chickens"
    if has_cow_clues and not has_chicken_clues:
        return "Farm:Cows"
        
    # LAYER 3: Volume & Weight Scale Interpretation
    if 'salt' in text_lower or 'vinegar' in text_lower:
        if any(scale_word in text_lower for scale_word in CATTLE_SCALE_WORDS):
            return "Farm:Cows"
        else:
            return "Farm:Chickens"

    # LAYER 4: Baseline keyword fallback search
    if any(kw in text_lower for kw in KEYWORDS_COWS):
        return "Farm:Cows"
    if any(kw in text_lower for kw in KEYWORDS_CHICKENS):
        return "Farm:Chickens"
        
    return "Farm:General"

def extract_basic_amount(text):
    """Finds formatted prices to pull out estimated totals."""
    amounts = re.findall(r'\b\d+\.\d{2}\b', text)
    if amounts:
        float_amounts = [float(a) for a in amounts]
        return f"${max(float_amounts):.2f}"
    return "[Amount Not Found]"

# =====================================================================
# BATCH WORKFLOW CORE
# =====================================================================
def process_receipts(downloaded_files, processed_dir, download_dir):
    """
    Unpacks multi-receipt shots, runs OCR scans, categorizes fields, 
    and writes to a single consolidated text log.
    """
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    
    # "a" Mode explicitly APPENDS data to prevent overwriting existing daily work
    with open(log_file_path, "a", encoding="utf-8") as log:
        log.write(f"\n==================================================\n")
        log.write(f"BATCH RUN DATE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"==================================================\n")
        
        for primary_file in downloaded_files:
            # Check if this file contains multiple sub-receipts
            sub_files = split_multiple_receipts(primary_file, download_dir)
            
            for file_path in sub_files:
                filename = os.path.basename(file_path)
                print(f"Scanning and extracting text from: {filename}...")
                
                try:
                    img = Image.open(file_path)
                    extracted_text = pytesseract.image_to_string(img)
                    
                    category = determine_subcategory(extracted_text)
                    estimated_total = extract_basic_amount(extracted_text)
                    
                    # Parse approximate Vendor (top clean text lines)
                    lines = [line.strip() for line in extracted_text.split('\n') if line.strip()]
                    vendor = lines if lines else "Unknown Vendor"
                    vendor = re.sub(r'[\\/*?:"<>|]', "", vendor)[:20]
                    
                    new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}___{filename}"
                    final_processed_path = os.path.join(processed_dir, new_filename)
                    
                    os.rename(file_path, final_processed_path)
                    
                    # Write formatted tracking data into the master daily file
                    log_block = (
                        f"File Name: {new_filename}\n"
                        f"Category:  {category}\n"
                        f"Vendor:    {vendor}\n"
                        f"Amount:    {estimated_total}\n"
                        f"--------------------------------------------------\n"
                    )
                    log.write(log_block)
                    print(f"Completed: {new_filename}")
                    
                except Exception as file_error:
                    print(f"Could not read {filename}: {file_error}")

# =====================================================================
# MAIN AUTOMATION ENTRY
# =====================================================================
if __name__ == "__main__":
    print("Free Farm Receipt Processor System Initialized.")
    download_folder, processed_folder = setup_folders()
    print("Synchronizing with email server...")
    new_paths = download_new_receipts(download_folder)
    
    if new_paths:
        print(f"Pulled {len(new_paths)} attachments. Initiating structural vision engines...")
        process_receipts(new_paths, processed_folder, download_folder)
        print(f"\nExecution Complete. Open your file ledger here to copy-paste: {processed_folder}")
    else:
        print("Inbox check clear. No unread receipt attachments detected.")
