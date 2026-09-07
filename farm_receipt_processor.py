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
# FREE VISION PRE-PROCESSING & SPLITTING ENGINES
# =====================================================================
def optimize_image_for_ocr(image_path):
    """
    Applies filters to neutralize dark phone shadows, brighten paper text,
    and erase heavy black line borders so Tesseract can see boxed totals.
    """
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    processed_img = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 15
    )

    cv2.imwrite(image_path, processed_img)
    return image_path

def split_multiple_receipts(image_path, download_dir):
    """
    Smarter receipt handler. Uses an incredibly high area threshold 
    so shadows and long layouts do not accidentally rip a single receipt apart.
    """
    image = cv2.imread(image_path)
    if image is None:
        return [image_path]
        
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    cropped_files = []
    receipt_count = 0
    base_name = os.path.basename(image_path)
    
    for contour in contours:
        if cv2.contourArea(contour) > 350000:  
            x, y, w, h = cv2.boundingRect(contour)
            
            if w > 150 and h > 150:
                cropped_img = image[y:y+h, x:x+w]
                cropped_filename = f"split_{receipt_count}_{base_name}"
                cropped_path = os.path.join(download_dir, cropped_filename)
                
                cv2.imwrite(cropped_path, cropped_img)
                cropped_files.append(cropped_path)
                receipt_count += 1
                
    if len(cropped_files) <= 1:
        return [image_path]
        
    try:
        os.remove(image_path)
    except OSError:
        pass
    return cropped_files

# =====================================================================
# EMAIL HARVESTER & ARCHIVER (IMAP FAILSAFES)
# =====================================================================
def download_new_receipts(download_dir):
    """Connects to email, harvests unread receipt attachments, and archives them."""
    saved_files = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select('"[Gmail]/All Mail"')
        
                status, data = mail.search(None, '(UNSEEN)')
        email_ids = []
        if status == 'OK' and data:
            # Step 1: Handle if the library wraps the data inside a list array container
            target_data = data[0] if isinstance(data, list) else data
            
            # Step 2: Convert raw bytes to standard text if necessary, then split into clean IDs
            if target_data:
                if isinstance(target_data, bytes):
                    target_data = target_data.decode('utf-8')
                email_ids = target_data.split()

    
        for e_id in email_ids:
            status, data = mail.fetch(e_id, '(RFC822)')
            if status != 'OK':
                continue
                
            raw_email = data
            msg = email.message_from_bytes(raw_email)
            
            has_valid_attachments = False
            
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                    continue
                    
                filename = part.get_filename()
                if filename and filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    filepath = os.path.join(download_dir, filename)
                    
                    if os.path.exists(filepath):
                        timestamp_prefix = datetime.now().strftime("%H%M%S_")
                        filename = timestamp_prefix + filename
                        filepath = os.path.join(download_dir, filename)
                        
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
# INTELLIGENT RULE INTERPRETER (CONTEXT SEARCH)
# =====================================================================
def determine_subcategory(text):
    """Context-aware category mapping rules."""
    text_lower = text.lower()
    
    if any(vendor in text_lower for vendor in VENDORS_CHICKEN_ONLY):
        return "Farm:Chickens"
    if any(vendor in text_lower for vendor in VENDORS_COW_ONLY):
        return "Farm:Cows"
        
    has_chicken_clues = any(kw in text_lower for kw in KEYWORDS_CHICKENS)
    has_cow_clues = any(kw in text_lower for kw in KEYWORDS_COWS)
    
    if has_chicken_clues and not has_cow_clues:
        return "Farm:Chickens"
    if has_cow_clues and not has_chicken_clues:
        return "Farm:Cows"
        
    if 'salt' in text_lower or 'vinegar' in text_lower:
        if any(scale_word in text_lower for scale_word in CATTLE_SCALE_WORDS):
            return "Farm:Cows"
        else:
            return "Farm:Chickens"

    if any(kw in text_lower for kw in KEYWORDS_COWS):
        return "Farm:Cows"
    if any(kw in text_lower for kw in KEYWORDS_CHICKENS):
        return "Farm:Chickens"
        
    return "Farm:General"

def extract_basic_amount(text):
    """Safeguard math price tool utilizing absolute maximum scanning methods."""
    amounts = re.findall(r'\b\d+\.\d{2}\b', text)
    if amounts:
        float_amounts = [float(a) for a in amounts]
        grand_total = max(float_amounts)
        return f"${grand_total:.2f}"
    return "[Amount Not Found]"

# =====================================================================
# BATCH WORKFLOW CORE
# =====================================================================
def process_receipts(downloaded_files, processed_dir, download_dir):
    """Unpacks sheets, optimizes contrast boundaries, scans text, and writes to log."""
    log_file_path = os.path.join(processed_dir, "Receipt_Data.txt")
    
    with open(log_file_path, "a", encoding="utf-8") as log:
        log.write(f"\n==================================================\n")
        log.write(f"BATCH RUN DATE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"==================================================\n")
        for primary_file in downloaded_files:
            sub_files = split_multiple_receipts(primary_file, download_dir)
            
            for file_path in sub_files:
                filename = os.path.basename(file_path)
                print(f"Optimizing image contrast and scanning: {filename}...")
                
                try:
                    optimized_path = optimize_image_for_ocr(file_path)
                    extracted_text = pytesseract.image_to_string(optimized_path)
                    
                    category = determine_subcategory(extracted_text)
                    estimated_total = extract_basic_amount(extracted_text)
                    
                    lines = [line.strip() for line in extracted_text.split('\n') if line.strip()]
                    
                    # Advanced Filter Loop: Look down top 5 lines for alphabetical store string
                    vendor = "Unknown Vendor"
                    for candidate_line in lines[:5]:
                        clean_candidate = re.sub(r'[^a-zA-Z\s]', '', candidate_line).strip()
                        if len(clean_candidate) > 3:
                            vendor = candidate_line
                            break
                            
                    vendor = re.sub(r'[\\/*?:"<>|]', "", vendor)[:20]
                    new_filename = f"{category.replace(':', '-')}__{vendor.replace(' ', '_')}___{filename}"
                    final_processed_path = os.path.join(processed_dir, new_filename)
                    
                    os.rename(optimized_path, final_processed_path)
                    
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
    new_paths = download_new_receipts(download_folder)
    
    if new_paths:
        process_receipts(new_paths, processed_folder, download_folder)
    else:
        print("Inbox check clear. No unread receipt attachments detected.")
