import csv
import os
import requests
import time
import re
import json
from urllib.parse import urlparse, unquote, urljoin
from pathlib import Path
from bs4 import BeautifulSoup
import logging
from typing import List, Dict, Tuple, Optional
import argparse  # --- 新增代码 --- 导入argparse

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.webdriver import WebDriver  # Import WebDriver type hint
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
import traceback  # For detailed error printing

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("webdriver_manager").setLevel(logging.WARNING)

# --- 辅助函数 (project_scraper) ---
def extract_text_with_spacing(element):
    """Recursively extracts text preserving approximate spacing."""
    result = ""
    if element is None:
        return result
    for content in element.contents:
        if hasattr(content, 'name') and content.name:  # if it's a tag
            result += extract_text_with_spacing(content)
        elif hasattr(content, 'string'):  # if it's NavigableString (text)
            result += content.string
        # Handle cases where content might be None or other types
    return result


def purge_description(description_list):
    """Cleans the list of description paragraphs."""
    # Remove items with 3 or fewer words
    description_list = [x for x in description_list if len(x.split()) > 3]
    # Remove specific boilerplate text
    description_list = [x for x in description_list if "You'll now receive updates based on what you follow!" not in x]
    # Remove "Check the" items
    description_list = [x for x in description_list if not x.startswith("Check the")]
    # Remove "Save this picture!" items
    description_list = [x for x in description_list if "Save this picture!" not in x]
    # Remove duplicates while preserving order
    seen = set()
    description_list = [x for x in description_list if not (x in seen or seen.add(x))]
    return description_list

# --- project_scraper 函数 (未修改) ---
def project_scraper(project_link: str) -> str:
    """
    Scrapes project details (metadata like architects, area, year, description)
    and saves them to [project_id]_details.json.
    Uses Requests, not Selenium.

    Args:
        project_link (str): The URL of the ArchDaily project page.

    Returns:
        str: Status ("downloaded" on success, "error" on failure).
    """
    print(f"--- Scraping Project Details for: {project_link} ---")
    headers = { # Add headers for requests
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    try:
        response = requests.get(project_link, headers=headers, timeout=20)
        response.raise_for_status() # Check for HTTP errors
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Network error fetching details for {project_link}: {e}")
        return "error"

    try:
        soup = BeautifulSoup(response.content, 'html.parser')
        print('Project details HTML parsed.')

        # Extract project ID from link for saving
        project_id = None
        try:
            path_parts = [part for part in urlparse(project_link).path.strip('/').split('/') if part]
            for part in path_parts:
                if part.isdigit():
                    project_id = part
                    break
        except Exception:
            pass # Ignore if ID extraction fails here, use fallback

        if not project_id:
             # Fallback: Try getting ID differently or use a placeholder
             project_id_match = re.search(r'/(\d+)/', project_link)
             if project_id_match:
                 project_id = project_id_match.group(1)
             else:
                 print("[WARN] Could not determine project ID for filename.")
                 project_id = "unknown_project" # Placeholder ID

        # --- Data Extraction Logic (similar to original) ---
        data = {'categories': [], 'city': None, 'country': None, 'architects': [], 'area': None, 'year': None, 'description': None}
        title_tag = soup.find(class_='afd-title-big--bmargin-big')
        project_title = title_tag.get_text(strip=True).split('/')[0].strip() if title_tag else "Untitled Project"

        category_container = soup.find(class_='afd-specs__header-category')
        if category_container: data['categories'] = [a.text.strip() for a in category_container.find_all('a')]

        location_container = soup.find(class_='afd-specs__header-location')
        if location_container:
            location_parts = location_container.get_text(strip=True).split(',')
            if location_parts: data['city'] = location_parts[0].strip()
            country_link = location_container.find('a')
            if country_link: data['country'] = country_link.text.strip()

        for item in soup.select('.afd-specs__item'):
            key_tag = item.find(class_='afd-specs__key')
            value_tag = item.find(class_='afd-specs__value')
            if not key_tag or not value_tag: continue
            key_text = key_tag.get_text(strip=True)
            if 'Architects' in key_text: data['architects'] = [a.text.strip() for a in value_tag.find_all('a')]
            elif 'Area' in key_text: data['area'] = value_tag.text.replace('m²', '').strip()
            elif 'Year' in key_text: data['year'] = value_tag.text.strip()
            # Add more fields here if needed (e.g., Manufacturers, etc.)

        # Description extraction
        description_paragraphs = []
        desc_section = soup.find('div', class_='the-content') # Target the main content div
        if desc_section:
            for paragraph in desc_section.find_all('p', recursive=False): # Find direct children paragraphs
                extracted_text = extract_text_with_spacing(paragraph)
                normalized_text = re.sub(r'\s+', ' ', extracted_text).strip()
                if normalized_text: # Avoid empty strings
                     description_paragraphs.append(normalized_text)
        else: # Fallback if specific content div isn't found
             for paragraph in soup.find_all('p'):
                 extracted_text = extract_text_with_spacing(paragraph)
                 normalized_text = re.sub(r'\s+', ' ', extracted_text).strip()
                 if normalized_text:
                     description_paragraphs.append(normalized_text)

        data['description'] = purge_description(description_paragraphs) # Clean the description list
        # --- End Data Extraction ---

        # Construct final dict
        project_dict = {
            'Project ID': project_id,
            'Project Title': project_title,
            'Categories': data['categories'] or None,
            'City': data['city'],
            'Country': data['country'],
            'Architects': data['architects'] or None,
            'Area': f"{data['area']} m²" if data['area'] else None,
            'Year': data['year'],
            'Project URL': project_link,
            'Description': data['description'] or None
        }
        project_dict = {k: v for k, v in project_dict.items() if v} # Remove keys with None/empty values

        # --- Save details JSON ---
        details_filename = f'{project_id}_details.json'
        # Ensure the base directory exists (needed if images aren't downloaded first)
        Path(project_id).mkdir(parents=True, exist_ok=True)
        details_filepath = Path(project_id) / details_filename

        with open(details_filepath, 'w', encoding='utf-8') as f:
            json.dump(project_dict, f, ensure_ascii=False, indent=2)

        print(f'Project details saved to: {details_filepath}')
        return "downloaded" # Use status consistent with original script

    except Exception as e:
        print(f"[ERROR] Error processing details for {project_link}: {e}")
        traceback.print_exc()
        return "error"


# --- scrape_gallery_thumbnails 函数 (未修改) ---
def scrape_gallery_thumbnails(driver: WebDriver, url: str) -> Tuple[bool, List[Dict[str, str]]]:
    """
    Scrapes gallery thumbnail information (URL and title) using a provided WebDriver.
    """
    gallery_items: List[Dict[str, str]] = []
    # WebDriver is now passed in, no internal creation/destruction

    try:
        print(f"Fetching project page with Selenium: {url}")
        driver.get(url)

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CLASS_NAME, "gallery-thumbs"))
            )
            print("Gallery thumbnails section loaded.")
        except TimeoutException:
            print("Timeout waiting for gallery thumbnails section.")
            # Consider if this should be a hard fail
            pass # Attempt to parse anyway

        # Use the passed driver to get page source
        initial_page_source = driver.page_source
        soup = BeautifulSoup(initial_page_source, "html.parser")

        thumbnail_items = soup.find_all("li", class_="gallery-thumbs-item")
        if not thumbnail_items:
             print("Could not find any 'gallery-thumbs-item' list items.")
             # It might be valid for a project to have no images/thumbnails
             return True, gallery_items # Return True but empty list

        print(f"Found {len(thumbnail_items)} potential thumbnail items.")
        processed_count = 0
        for i, li in enumerate(thumbnail_items):
            try:
                link = li.find("a", class_="gallery-thumbs-link")
                if not link: continue
                href = link.get("href")
                if not href: continue

                full_href = urljoin(url, href)
                title = link.get("title", "").strip()
                if not title:
                    img = link.find("img")
                    if img: title = img.get("alt", "").strip()
                title = title if title else f"Untitled_{i+1}"

                if not full_href or full_href == url: continue # Skip self-links

                item = {"href": full_href, "title": title}
                gallery_items.append(item)
                processed_count += 1
            except Exception as e:
                print(f"  Error processing thumbnail item {i+1}: {str(e)}")
                continue # Skip this item

        print(f"Gallery thumbnail scraping complete. Successfully extracted {processed_count} items.")
        # Success is true if the process ran, even if 0 items were found/processed
        return True, gallery_items

    except WebDriverException as e:
        print(f"[ERROR] WebDriver error during thumbnail scraping for {url}: {e}")
        return False, gallery_items
    except Exception as e:
        print(f"[ERROR] Unexpected error in scrape_gallery_thumbnails for {url}: {e}")
        traceback.print_exc()
        return False, gallery_items
    # No driver.quit() here - managed globally


# --- download_gallery_image 函数 (未修改) ---
def download_gallery_image(page_url: str, save_directory: Path, base_filename: str) -> Tuple[bool, Optional[List[str]], Optional[str], Optional[str]]:
    # --- (Content of this function remains the same as in the previous version) ---
    # ... (It uses requests, parses JSON, downloads image, returns tuple) ...
    success = False
    tags = None
    caption = None
    target_image_info = None
    final_filename = None

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        logging.info(f"Fetching image page HTML: {page_url}")
        response = requests.get(page_url, headers=headers, timeout=15)
        response.raise_for_status()
        logging.info("HTML fetched.")

        soup = BeautifulSoup(response.text, 'html.parser')
        gallery_div = soup.find('div', id='gallery-items')
        if not gallery_div:
            logging.error("No gallery container ('div#gallery-items').")
            return success, tags, caption, final_filename

        data_images_json = gallery_div.get('data-images')
        default_image_id_fragment = gallery_div.get('data-id')
        if not data_images_json:
            logging.error("No 'data-images' attribute.")
            return success, tags, caption, final_filename

        try:
            image_data = json.loads(data_images_json)
            if not isinstance(image_data, list) or not image_data:
                 logging.error("'data-images' not a valid list.")
                 return success, tags, caption, final_filename
        except json.JSONDecodeError as e:
            logging.error(f"JSON parse error: {e}")
            return success, tags, caption, final_filename

        parsed_url = urlparse(page_url)
        url_fragment = parsed_url.fragment
        target_image_info = None

        if url_fragment:
            target_id = url_fragment.split('-')[0]
            for img_info in image_data:
                if 'link' in img_info and target_id in img_info['link']:
                     target_image_info = img_info
                     break
            if not target_image_info:
                 logging.warning(f"Fragment '{target_id}' not matched.")

        if not target_image_info: # Fallback
            if default_image_id_fragment:
                for img_info in image_data:
                     if 'link' in img_info and default_image_id_fragment in img_info['link']:
                         target_image_info = img_info
                         break
                if not target_image_info:
                     logging.warning(f"Default ID {default_image_id_fragment} not matched.")
                     if image_data: target_image_info = image_data[0] # Use first
            else:
                logging.warning("No fragment/default ID. Using first image.")
                if image_data: target_image_info = image_data[0] # Use first

        if not target_image_info:
             logging.error("Cannot determine target image.")
             return success, tags, caption, final_filename

        image_url = target_image_info.get('url_large') or target_image_info.get('url_slideshow')
        caption = target_image_info.get('caption', 'No Caption')
        raw_tags = target_image_info.get('tags', [])
        tags = [tag.get('name', '').strip() for tag in raw_tags if isinstance(tag, dict) and 'name' in tag]

        if not image_url:
            logging.error(f"Target image missing URL (Link: {target_image_info.get('link')}).")
            return success, tags, caption, final_filename

        parsed_image_url = urlparse(image_url)
        image_path_part = Path(unquote(parsed_image_url.path))
        extension = image_path_part.suffix
        if not extension or len(extension) > 5:
             extension = ".jpg"
             logging.warning(f"Using default .jpg for {image_url}")

        final_filename = f"{base_filename}{extension}"
        save_path = save_directory / final_filename
        logging.info(f"Target save path: {save_path}")

        logging.info(f"Downloading image: {image_url}...")
        img_response = requests.get(image_url, stream=True, headers=headers, timeout=30)
        img_response.raise_for_status()

        with open(save_path, 'wb') as f:
            for chunk in img_response.iter_content(chunk_size=8192):
                f.write(chunk)

        logging.info(f"Image download SUCCESS.")
        success = True

    except requests.exceptions.Timeout: logging.error("Request timed out.")
    except requests.exceptions.HTTPError as e: logging.error(f"HTTP Error: {e.response.status_code}")
    except requests.exceptions.RequestException as e: logging.error(f"Network error: {e}")
    except IOError as e: logging.error(f"File IO error: {e}")
    except Exception as e: logging.error(f"Unexpected error: {e}", exc_info=False)

    finally:
        return success, tags, caption, final_filename


# --- process_project_images 函数 (未修改) ---
def process_project_images(driver: WebDriver, project_url: str) -> Tuple[bool, Optional[Path]]:
    """
    Processes images for a single ArchDaily project using a provided WebDriver:
    scrapes thumbnails, downloads images, saves image metadata JSON.

    Args:
        driver (WebDriver): The shared Selenium WebDriver instance.
        project_url (str): The URL of the ArchDaily project page.

    Returns:
        Tuple[bool, Optional[Path]]: Overall success status for image processing,
                                     and the Path object to the project folder if successful.
    """
    print(f"--- Processing Project Images for: {project_url} ---")
    overall_image_success = False # Track if at least one image downloads

    # --- Extract Project ID and Set Up Base Folder ---
    project_id = None
    try:
        path_parts = [part for part in urlparse(project_url).path.strip('/').split('/') if part]
        for part in path_parts:
            if part.isdigit():
                project_id = part
                break
    except Exception as e:
        print(f"[ERROR] Error extracting project ID for images: {e}")
        return overall_image_success, None

    if not project_id:
        print(f"[ERROR] Could not extract project ID for images: {project_url}.")
        return overall_image_success, None

    base_download_folder = Path(project_id)
    print(f"Image Project ID: {project_id}")
    print(f"Image download directory: '{base_download_folder.resolve()}'")

    try:
        base_download_folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[ERROR] Error creating/accessing image directory '{base_download_folder}': {e}.")
        return overall_image_success, None

    # --- Scrape Thumbnails using shared driver ---
    print(f"Scraping thumbnails...")
    # Pass the shared driver to the modified scrape_gallery_thumbnails
    success_scrape, gallery_items = scrape_gallery_thumbnails(driver, project_url)

    if not success_scrape:
        print("Thumbnail scraping failed. Cannot proceed with image downloads.")
        return overall_image_success, base_download_folder # Return folder path but False status
    if not gallery_items:
        print("No gallery items found to download.")
        # Consider this a success in terms of processing, just nothing to download
        return True, base_download_folder

    # --- Iterate and Download ---
    print(f"--- Starting image downloads for {len(gallery_items)} items ---")
    all_metadata = []
    successful_downloads = 0
    for index, item in enumerate(gallery_items):
        item_url = item.get('href')
        item_title = item.get('title', f'image_{index+1}')

        print(f"\n>>> Downloading Item {index+1}/{len(gallery_items)}: '{item_title}' <<<")

        if not item_url:
            print("    Skipping: Missing URL.")
            continue

        base_filename = f"{project_id}_{index+1:02d}"

        success_download, tags, caption, final_filename = download_gallery_image(
            item_url,
            base_download_folder,
            base_filename
        )

        if success_download and final_filename:
            print(f"    SUCCESS: Saved as {final_filename}")
            metadata_entry = {
                "filename": final_filename,
                "tags": tags if tags else [],
                "caption": caption if caption else ""
            }
            all_metadata.append(metadata_entry)
            successful_downloads += 1
            overall_image_success = True # Mark overall success if at least one downloads
        else:
            print(f"    FAILED")

        time.sleep(0.5) # Delay

    print(f"\n--- Image download process complete: {successful_downloads}/{len(gallery_items)} successful. ---")

    # --- Save Image Metadata JSON ---
    if all_metadata:
        metadata_filepath = base_download_folder / f"{project_id}_images.json" # New filename
        print(f"Saving image metadata to {metadata_filepath}")
        try:
            with open(metadata_filepath, 'w', encoding='utf-8') as f:
                json.dump(all_metadata, f, indent=4, ensure_ascii=False)
            print("Image metadata saved.")
        except IOError as e:
            print(f"[ERROR] Failed to save image metadata JSON: {e}")
    else:
        print("No image metadata collected.")

    print(f"--- Finished processing images for project {project_id} ---")
    # Return overall success status and the folder path
    return overall_image_success, base_download_folder


# --- update_csv_status 函数 (未修改) ---
def update_csv_status(csv_file, project_code, status):
    rows = []
    try:
        with open(csv_file, mode='r', newline='', encoding='utf-8') as infile:
            reader = csv.reader(infile)
            header = next(reader)
            try:
                 status_index = header.index('status')
                 # --- 新增代码 --- 假设 project_code 总是第一列 (index 0)
                 code_index = 0 
                 # ------------------
            except ValueError:
                 print(f"[ERROR] 'status' column not found in CSV header: {header}")
                 return # Cannot update status
            rows.append(header)
            for row in reader:
                 # --- 修改后的代码 --- 确保使用 code_index 比较
                if row and len(row) > code_index and row[code_index] == project_code: # Check if row is not empty and has code
                # ------------------
                    # Ensure row has enough columns before accessing status_index
                    if len(row) > status_index:
                        row[status_index] = status
                    else:
                        # Handle rows that might be shorter than the header
                        print(f"[WARN] Row for {project_code} is too short, cannot update status.")
                        # Optionally append empty strings to match header length
                        row.extend([''] * (len(header) - len(row)))
                        row[status_index] = status # Try updating now
                rows.append(row)
    except FileNotFoundError:
        print(f"[ERROR] CSV file not found: {csv_file}")
        return
    except Exception as e:
        print(f"[ERROR] Error reading CSV file {csv_file}: {e}")
        return

    try:
        # Use temporary file for safer writing
        temp_file = Path(csv_file).with_suffix('.tmp')
        with open(temp_file, mode='w', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)
            writer.writerows(rows)
        # Replace original file with temporary file
        os.replace(temp_file, csv_file)
        print(f"Updated status for {project_code} to '{status}' in {csv_file}")
    except Exception as e:
        print(f"[ERROR] Error writing updated CSV file {csv_file}: {e}")
        if temp_file.exists():
             os.remove(temp_file) # Clean up temp file on error


# --- remove_csv_status 函数 (未修改) ---
def remove_csv_status(csv_file):
    # remove all the status in the csv file
    rows = []
    try:
        with open(csv_file, mode='r', newline='', encoding='utf-8') as infile:
            reader = csv.reader(infile)
            header = next(reader)
            try:
                 status_index = header.index('status')
            except ValueError:
                 print(f"[ERROR] 'status' column not found in CSV header: {header}")
                 return
            rows.append(header)
            for row in reader:
                 if row: # Check if row is not empty
                      if len(row) > status_index:
                           row[status_index] = "" # Clear the status
                      else:
                           # Handle short rows if necessary, or just skip status update
                           pass
                 rows.append(row) # Append row even if it was short/empty
    except FileNotFoundError:
        print(f"[ERROR] CSV file not found: {csv_file}")
        return
    except Exception as e:
        print(f"[ERROR] Error reading CSV file {csv_file} for status removal: {e}")
        return

    try:
        # Use temporary file for safer writing
        temp_file = Path(csv_file).with_suffix('.tmp')
        with open(temp_file, mode='w', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)
            writer.writerows(rows)
        os.replace(temp_file, csv_file)
        print(f"Cleared all statuses in {csv_file}")
    except Exception as e:
        print(f"[ERROR] Error writing CSV file {csv_file} after clearing statuses: {e}")
        if temp_file.exists():
             os.remove(temp_file)


# --- 主执行逻辑 (已修改) ---
if __name__ == "__main__":
    
    # --- 新增代码 ---
    # 1. 设置参数解析器
    parser = argparse.ArgumentParser(description="ArchDaily Project Scraper")
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help="Run in debug mode: process only the first non-skipped item and do not update CSV status."
    )
    args = parser.parse_args()
    debug_mode = args.debug

    if debug_mode:
        print("--- RUNNING IN DEBUG MODE ---")
        print("Will process only one item and will not update CSV status.")
    # --- 新增代码结束 ---

    csv_file = './archdaily_projects.csv'
    base_url = 'https://www.archdaily.com' # Define base_url needed for link construction

    # --- Initialize Shared WebDriver ---
    driver = None
    try:
        print("Initializing Shared WebDriver for the session...")
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--log-level=3')
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        os.environ['WDM_LOG_LEVEL'] = '0'

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        print("Shared WebDriver Initialized.")

        # --- Optional: Reset all statuses before starting ---
        # print(f"\nClearing previous statuses in {csv_file}...")
        # remove_csv_status(csv_file)
        # print("Statuses cleared.")
        # --- End Optional Reset ---


        # --- Read CSV and Process Projects ---
        print(f"\nReading projects from {csv_file}...")
        try:
            with open(csv_file, 'r', encoding='utf-8') as file:
                reader = csv.reader(file)
                header = next(reader)
                try:
                    status_index = header.index('status')
                    # Assuming project code is the first column
                    code_index = 0
                except ValueError:
                    print("[ERROR] CSV file must contain 'status' column. Exiting.")
                    if driver: driver.quit() # --- 新增代码 --- 退出前清理driver
                    exit()

                rows = list(reader) # Read all rows into memory for easier processing

        except FileNotFoundError:
            print(f"[ERROR] CSV file not found: {csv_file}. Exiting.")
            if driver: driver.quit() # --- 新增代码 --- 退出前清理driver
            exit()
        except Exception as e:
            print(f"[ERROR] Failed to read CSV file {csv_file}: {e}. Exiting.")
            if driver: driver.quit() # --- 新增代码 --- 退出前清理driver
            exit()

        print(f"Found {len(rows)} projects in CSV.")

        for i, row in enumerate(rows):
            if not row: # Skip empty rows
                 print(f"Skipping empty row {i+1}")
                 continue
            try:
                project_code = row[code_index]
                status = row[status_index] if len(row) > status_index else ""
            except IndexError:
                 print(f"[WARN] Skipping row {i+1} due to insufficient columns: {row}")
                 continue

            print(f"\n{'='*10} Processing Project {i+1}/{len(rows)}: {project_code} {'='*10}")

            # Skip based on status
            skip_statuses = ['downloaded', 'error', 'incomplete', 'duplicate', 'delete'] # Add more if needed
            if status.lower() in skip_statuses:
                print(f"Skipping project {project_code} (Status: '{status}').")
                continue

            # Construct full project link
            # Ensure project_code doesn't have leading/trailing slashes if base_url ends with one
            project_link = f"{base_url.strip('/')}/{project_code.strip('/')}"
            print(f"Project URL: {project_link}")

            # --- Step 1: Scrape Project Details ---
            scraper_status = project_scraper(project_link) # Uses Requests

            # --- Step 2: Process Project Images (if details scraped successfully) ---
            downloader_overall_success = False
            if scraper_status == "downloaded":
                # Pass the shared driver here
                downloader_overall_success, _ = process_project_images(driver, project_link)
            else:
                print("Skipping image download because project details scraping failed.")

            # --- Step 3: Determine Final Status and Update CSV ---
            final_status = "error" # Default to error
            if scraper_status == "downloaded":
                if downloader_overall_success:
                    final_status = "downloaded" # Both parts succeeded
                else:
                    # Scraper OK, but downloader failed or found no images to download successfully
                    final_status = "incomplete"
            # Keep "error" if scraper_status was "error"

            print(f"Final status for {project_code}: {final_status}")
            
            # --- 修改后的代码 ---
            # 2. 检查 debug_mode
            if not debug_mode:
                update_csv_status(csv_file, project_code, final_status)
            else:
                print(f"Debug mode: Skipping status update for {project_code}.")
            # --- 修改结束 ---

            # Optional short delay between projects
            time.sleep(1)

            # --- 新增代码 ---
            # 3. 检查 debug_mode 以便退出循环
            if debug_mode:
                print("\nDebug mode active. Stopping after first processed item.")
                break # 退出 for 循环
            # --- 新增代码结束 ---

        print("\nFinished processing all projects from CSV.")

    except Exception as e:
        print(f"\nAn unexpected error occurred during the main processing loop: {e}")
        traceback.print_exc()
    finally:
        # --- Clean Up Shared WebDriver ---
        if driver:
            print("\nQuitting Shared WebDriver...")
            try:
                driver.quit()
                print("Shared WebDriver quit successfully.")
            except Exception as e:
                print(f"Error quitting Shared WebDriver: {e}")