import csv
import os
import requests
import time
import re
import json
import random
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
def parse_cookie_string(cookie_str):
    """Helper to parse raw cookie string into Selenium dict format"""
    cookies = []
    if not cookie_str:
        return cookies
    for item in cookie_str.split(';'):
        if '=' in item:
            name, value = item.strip().split('=', 1)
            cookies.append({'name': name, 'value': value})
    return cookies


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
    description_list = [x for x in description_list if "If you want to make the best of your experience on our site, sign-up." not in x]
    # Remove "Check the" items
    description_list = [x for x in description_list if not x.startswith("Check the")]
    # Remove "Save this picture!" items
    description_list = [x for x in description_list if "Save this picture!" not in x]
    # Remove duplicates while preserving order
    seen = set()
    description_list = [x for x in description_list if not (x in seen or seen.add(x))]
    return description_list

# --- project_scraper 函数 (已修改) ---

# --- project_scraper 函数 (极速版: 依赖Cookie, 去除随机等待) ---
def project_scraper(project_link: str, driver: WebDriver) -> str:
    """
    Scrapes project details using Selenium.
    Assumes valid Cookies are injected via the main driver, so no anti-bot delays are needed.
    """
    print(f"--- Scraping Project Details for: {project_link} ---")
    
    try:
        # 1. 直接访问页面 (无随机延时)
        driver.get(project_link)

        # 2. 等待页面加载 (仅保留10s等待逻辑)
        # 有了Cookie，服务器通常会立刻响应，这里是为了防止网络波动
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.the-content"))
            )
        except TimeoutException:
            print("[WARN] Timeout waiting for 'the-content' (10s). Proceeding with available HTML.")

        # 3. 获取 HTML
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        print('Project details HTML parsed.')

        # --- 以下数据提取逻辑保持不变 ---
        
        # Extract project ID
        project_id = None
        try:
            path_parts = [part for part in urlparse(project_link).path.strip('/').split('/') if part]
            for part in path_parts:
                if part.isdigit():
                    project_id = part
                    break
        except Exception: pass 

        if not project_id:
             project_id_match = re.search(r'/(\d+)/', project_link)
             project_id = project_id_match.group(1) if project_id_match else "unknown_project"

        # Metadata Extraction
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

        # --- Description Extraction ---
        description_paragraphs = []
        desc_section = soup.find('div', class_='the-content')
        
        # 优先尝试从 the-content 提取
        if desc_section:
            raw_text = desc_section.get_text(separator='\n')
            lines = raw_text.split('\n')
            for line in lines:
                normalized_text = re.sub(r'\s+', ' ', line).strip()
                if len(normalized_text) > 10: 
                    description_paragraphs.append(normalized_text)
        
        # Fallback
        if not description_paragraphs:
             # print("[INFO] Fallback: Trying global p tags.") # 可选：如果不需要太啰嗦的日志可以注释掉
             for paragraph in soup.find_all('p'):
                 extracted_text = extract_text_with_spacing(paragraph)
                 normalized_text = re.sub(r'\s+', ' ', extracted_text).strip()
                 if len(normalized_text) > 10:
                     description_paragraphs.append(normalized_text)

        # 清洗描述
        data['description'] = purge_description(description_paragraphs)

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
        project_dict = {k: v for k, v in project_dict.items() if v}

        # Save
        details_filename = f'{project_id}_details.json'
        base_data_folder = Path(f"data/{project_id}")
        base_data_folder.mkdir(parents=True, exist_ok=True)
        details_filepath = base_data_folder / details_filename

        with open(details_filepath, 'w', encoding='utf-8') as f:
            json.dump(project_dict, f, ensure_ascii=False, indent=2)

        print(f'Project details saved to: {details_filepath}')
        return "downloaded"

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


# --- process_project_images 函数 (已修改) ---
def process_project_images(driver: WebDriver, project_url: str) -> Tuple[bool, Optional[Path]]:
    """
    Processes images for a single ArchDaily project using a provided WebDriver:
    scrapes thumbnails, downloads images, saves image metadata JSON
    to the data/[project_id] folder.

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

    # --- 修改后的代码 (2/2) ---
    # 将基础文件夹设置为 data/[project_id]
    base_download_folder = Path(f"data/{project_id}")
    # --- 修改结束 ---
    
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


# --- 主执行逻辑 (未修改) ---
if __name__ == "__main__":
    
    # --- 新增代码 ---
    # 1. 设置参数解析器
    parser = argparse.ArgumentParser(description="ArchDaily Project Scraper")
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help="Run in debug mode: process only the first non-skipped item and do not update CSV status."
    )
    parser.add_argument(
        '-t', '--text-only',
        action='store_true',
        help="Only scrape text details, skip image downloading. Allows re-processing 'downloaded' items."
    )
    args = parser.parse_args()
    debug_mode = args.debug
    text_only_mode = args.text_only

    if debug_mode:
        print("--- RUNNING IN DEBUG MODE ---")
        print("Will process only one item and will not update CSV status.")
    if text_only_mode:
        print("--- RUNNING IN TEXT-ONLY MODE ---")
        print("Will skip image downloading and only scrape text details.")
    # --- 新增代码结束 ---

    csv_file = './archdaily_projects.csv' # --- 这个路径保持不变 ---
    base_url = 'https://www.archdaily.com' # Define base_url needed for link construction

    MY_COOKIE_STR = """LANG=en_US; _ga=GA1.2.887640547.1738334817; __io=7bf2e96a2.9ddc57aad_1738334870114; _hjSessionUser_270045=eyJpZCI6IjY0MGMyNzQxLWYyN2EtNTI2Yi1iMTc0LTEyZWE2Zjk3NWEyZiIsImNyZWF0ZWQiOjE3MzgzMzQ4MTc1MDUsImV4aXN0aW5nIjp0cnVlfQ==; _pcid=%7B%22browserId%22%3A%22m6kvpazbdgrok62b%22%7D; cX_P=m6kvpazbdgrok62b; cX_G=cx%3A3nlewowj7kqap38inmj3ksfmyy%3A2pr9vclj2kr3; __pat=3600000; __pnahc=0; _sharedID=50c02da5-e751-4d07-8511-0fc61e7b5c4c; _fbp=fb.1.1762700354708.664573266415830537; mm-user-id=ZfLNgsHlccKey3qn; pushalert_6772_1_pv=1; _gid=GA1.2.279841104.1763045737; __io_unique_25768=13; __io_unique_34360=13; ccuid=71fe9a4a-9e93-4e62-adbd-0f32c7d41594; ccsid=9a1917a5-721f-4e61-aa65-6ed95b68a0fb; _lr_sampling_rate=0; __io_first_source=buy-eu.piano.io; ad-consented_at=2030-11-12T14:58:08.355Z; _pc_payment_details=null; ad_session=94872b597205a714944794919c08b37882ffee91fee6de7fc39dabc633a3eedb; ad_acui=32118956; __io_r=accounts.google.com; __io_pr_utm_campaign=%7B%22referrerHostname%22%3A%22accounts.google.com%22%7D; __tae=1763046618162; _ga_545645PXL7=GS2.2.s1763045738$o3$g1$t1763047203$j60$l0$h0; __pvi=eyJpZCI6InYtbWh4a3M5OTF3dWw4dHVsdCIsImRvbWFpbiI6Ii5hcmNoZGFpbHkuY29tIiwidGltZSI6MTc2MzA0NzIwNDMwOX0%3D; g_state={"i_l":0,"i_ll":1763047204885,"i_b":"AD2qDIIp23l1zkKxs0XFPRsnGTgouIZRbyvTC4kMVSk"}; __tbc=%7Bkpex%7DHyzPJrqRQ5QmyCzRJw_a1mmjaE2egHY-KDNjn_y0swsc5HtwVSHrqbPz3TmHEP0C; _pctx=%7Bu%7DN4IgrgzgpgThIC5QFYAMB2ZAWdBGRoADjFAGYCWAHoiADYDuAjiADQgAuAnoVDQGoANEAF9hbSLADK7AIbtINABYyIAQQDG7cgDcoG9VAjw2EcuygBJACY1kADmQA2XAE50qOy4DMAJhdY7VFwfZBEgA; xbc=%7Bkpex%7DkhspAy1qqDOc5qs1xs9gSH6mQrFobcR0SWszrTn0tBs2kVviQvlozzxhuBg41vJQ0uB_8_lDeLt-KoikvesuBtgi8n_du50jbUOC8QUH1-wQwb9MmaeoRfwjYj-DWSuY5IInM0l3890P84TQUx9kI0KCLmzjBeAcs8Ar8TUg5GKD13vxd-t77NQoE1OxQWIgp9G571ExO_TTBTI10VPQQI6urBj4IvTTs5e3gALpRqizCa4mBFuYeLFxAwZY9LDAxFy9lcVj9sGZcEFt8Zh9cT4QUgsVe161XWNFkoYYoZyseVX2ryAl3Uf6d7A5qDwVUvKB4bRyiKOaMv_XmjJcVuqQBqxm1kVqHMkYXdv9Dc9OQFKl4PJnRkPOvVx2SGOyFP7GLBDiEV7wea4pUOtts65pUd4LDVWnGMS145qnk-2EgEvg_o8zNnb2mf7TRST8LgUn1CymliAT2318eebmT2N81XfOnVN4l-PzGo11tOeYBsbWW-MLSdSC9B9h0CHb3dj34ernarTQNw9Vs29h39BGXeK6NeW04C6iympI6Q6vkxXIepYZAeGG4_DRO5SM8dsI_5ok_-EvCJYe7lCz9jGNkY1SUCKTqALUmvJQEVRqItFauGqNZz1xHEqfC2Vth9sHpbiayxPuOSUVq0POXzIvdndEQbFYvVH7LGatZqEGQk9_aBqdQ1aep6U16EkG; _sharedID_cst=niwbLLosaQ%3D%3D; __io_lv=1763047666248"""
    # --- Initialize Shared WebDriver ---
    driver = None
    try:
        print("Initializing Shared WebDriver for the session...")
        options = Options()
        # options.add_argument('--headless')
        # --- [新增] 关键伪装设置 ---
        # 1. 禁用 "自动化控制" 特征
        options.add_argument("--disable-blink-features=AutomationControlled") 
        # 2. 伪装 User-Agent (确保和真实浏览器一致)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
        # 3. 窗口大小设置 (Headless模式下有时候默认窗口太小触发移动端布局)
        options.add_argument("--window-size=1920,1080")
        # -------------------------
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

        # [关键修改 2] 注入 Cookie
        print("Injecting Cookies...")
        try:
            # 1. 必须先访问一下目标域名，才能设置 Cookie
            driver.get("https://www.archdaily.com/404") # 访问一个不存在的页面或者主页都行，只要是同域名
            
            # 2. 解析并添加 Cookie
            cookie_list = parse_cookie_string(MY_COOKIE_STR)
            for cookie in cookie_list:
                try:
                    driver.add_cookie(cookie)
                except Exception as e:
                    # 有些特定字段可能会报错，忽略即可
                    pass
            
            print(f"Successfully injected {len(cookie_list)} cookies.")
            
            # 3. 刷新页面让 Cookie 生效 (或者直接开始后面的任务)
            driver.refresh()
            time.sleep(2)
            
        except Exception as e:
            print(f"[WARN] Failed to inject cookies: {e}")
        
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
            skip_statuses = ['error', 'incomplete', 'duplicate', 'delete'] # Add more if needed
            if not text_only_mode:
                skip_statuses.append('downloaded')

            if status.lower() in skip_statuses:
                print(f"Skipping project {project_code} (Status: '{status}').")
                continue

            # Construct full project link
            # Ensure project_code doesn't have leading/trailing slashes if base_url ends with one
            project_link = f"{base_url.strip('/')}/{project_code.strip('/')}"
            print(f"Project URL: {project_link}")

            # --- Step 1: Scrape Project Details ---
            scraper_status = project_scraper(project_link,driver) # Uses Requests

            # --- Step 2: Process Project Images (if details scraped successfully) ---
            downloader_overall_success = False
            if scraper_status == "downloaded":
                # --- 修改后的逻辑: 检查是否是 text-only 模式 ---
                if text_only_mode:
                    print("Text-only mode enabled: Skipping image download process.")
                    # 假定下载成功，以免将状态改为 incomplete。
                    # 如果原本是 downloaded，这样可以保持状态不变（如果后续更新CSV开启的话）。
                    downloader_overall_success = True 
                else:
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