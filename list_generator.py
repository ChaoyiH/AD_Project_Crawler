import pandas as pd
import concurrent.futures
import threading
import time
import os
import logging
from typing import Optional, List # 确保 List 被导入
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import re

# --- 1. 配置日志 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("webdriver_manager").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# --- 2. 定义关键词和URL (已修改) ---
logging.info("使用精简的关键词和分类策略生成URL...")

# 只保留高相关性的科学领域
keywords = [
    "Technology",
    "Biology",
    "Zoology",
    "Geology",
    "Paleontology",
    "Observatory", # 天文台 (比 Astronomy 精确)
    "Exploratorium" # 探索馆 (特定类型)
]

base_url = "https://www.archdaily.com/search/projects/categories/museum"
urls = []
for keyword in keywords:
    # 强制在 "museum" 分类下搜索
    urls.append(f"{base_url}?q={keyword}")

# 只保留被验证过 100% 准确的分类
# (移除了 categories/science-center，因为它会抓取科研机构)
urls.append("https://www.archdaily.com/search/projects/categories/planetarium")

logging.info(f"Generated {len(urls)} URLs to scrape.")


# --- 3. 线程安全的DataFrame存储 (未修改) ---
class ThreadSafeDataFrame:
    def __init__(self):
        self._lock = threading.Lock()
        self._df = pd.DataFrame(columns=['project_id', 'link', 'keyword', 'status'])

    def append(self, new_df):
        with self._lock:
            if not self._df.empty and not self._df.columns.equals(new_df.columns):
                logging.warning("DataFrame columns mismatch. This should not happen.")
            self._df = pd.concat([self._df, new_df], ignore_index=True)

    def get(self):
        with self._lock:
            return self._df.copy()

shared_projects = ThreadSafeDataFrame()


# --- 4. 每个线程的爬取任务 (未修改) ---
def crawl_task(url: str) -> Optional[str]:
    logging.info(f"Starting task for: {url}")
    os.environ['WDM_LOG_LEVEL'] = '0' 
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--log-level=3')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')

    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(url)

        WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, '//div[@data-insights-category="search-layout-toggler"]'))
        ).click()
        
        last_height = driver.execute_script("return document.body.scrollHeight")
        scroll_attempts = 0
        max_scroll_attempts = 3 

        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3) 
            new_height = driver.execute_script("return document.body.scrollHeight")
            
            if new_height == last_height:
                scroll_attempts += 1
                if scroll_attempts >= max_scroll_attempts:
                    logging.info(f"Reached bottom (or page stuck) for {url}")
                    break
            else:
                scroll_attempts = 0 
            last_height = new_height

        project_links_elements = driver.find_elements(By.CLASS_NAME, 'afd-title--black-link')
        project_links = [link.get_attribute('href') for link in project_links_elements]
        
        if not project_links:
            logging.warning(f"No results found for {url}")
            return None

        project_ids = []
        valid_links = []
        for link in project_links:
            match = re.search(r'/(\d+)/', link)
            if match:
                project_ids.append(match.group(1))
                valid_links.append(link)
            else:
                logging.warning(f"Could not parse project_id from link: {link}")

        if not valid_links:
            logging.warning(f"No valid project IDs found for {url}")
            return None
            
        # 提取 'keyword' 来源
        keyword_source = url
        if "?q=" in url:
             keyword_source = "q=" + url.split("?q=")[-1]
        else:
             keyword_source = "category/" + url.split("/")[-1]

        new_projects = pd.DataFrame({
            'project_id': project_ids,
            'link': valid_links,
            'keyword': keyword_source, # 保存更清晰的来源
            'status': ''
        })

        shared_projects.append(new_projects)
        
    except TimeoutException:
        logging.error(f"Timeout occurred for {url}")
    except Exception as e:
        logging.error(f"Error processing {url}: {str(e)}", exc_info=False) 
    finally:
        if driver:
            driver.quit()

    return url

# --- 5. 主逻辑 (再次修改) ---
def main(urls_to_crawl: list):
    max_workers = min(10, len(urls_to_crawl)) 
    logging.info(f"Starting ThreadPoolExecutor with {max_workers} workers...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(crawl_task, url): url for url in urls_to_crawl}

        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                if result:
                    logging.info(f"Completed task for: {result}")
            except Exception as e:
                logging.error(f"Exception raised by task for {url}: {str(e)}")

    logging.info("All tasks completed. Processing results...")
    
    projects = shared_projects.get()

    if projects.empty:
        logging.warning("No results found for any URL. CSV will not be created.")
        return None

    logging.info(f"Total projects found before deduplication: {len(projects)}")
    
    # 1. 去重
    projects = projects.drop_duplicates('project_id', keep='first')
    logging.info(f"Total unique projects found: {len(projects)}")
    
    # --- 2. 优化的过滤逻辑 (已修改) ---
    
    # 过滤器 1: 标记艺术馆 (Art Museums)
    # 我们只查找链接中明确包含 '-art-museum' 或 '-art-gallery' 或 '-contemporary-art' 的项目
    art_pattern = r'-art-museum|-art-gallery|-contemporary-art'
    art_mask = projects['link'].str.contains(art_pattern, case=False)
    projects.loc[art_mask, 'status'] = 'delete'
    logging.info(f"Marked {art_mask.sum()} projects (Art Museums/Galleries) as 'delete'.")

    # 过滤器 2: 标记纯科研机构 (Research Labs/Institutes)
    # (我们只在尚未被标记为 'delete' 的项目中搜索)
    # 移除了 'park' 和 'engineering'，保留了更明确的词
    research_words = [
        'laboratory', 
        'institute', 
        'foundation', 
        'skylab', 
        'research', 
        'biocenter', 
        'bioengineering',
        'tech-center', # 保留 'tech-center'，因为它在 'science-center' 分类中引起了噪音
        'school'
    ]
    
    # \b 确保我们匹配的是完整的单词
    research_pattern = r'\b(' + '|'.join(re.escape(word) for word in research_words) + r')\b'
    
    # 同样只在未被标记为 'delete' 的项目中应用此规则
    research_mask = projects['link'].str.contains(research_pattern, case=False) & (projects['status'] != 'delete')
    projects.loc[research_mask, 'status'] = 'delete'
    logging.info(f"Marked {research_mask.sum()} additional projects (Labs/Institutes) as 'delete'.")
    
    return projects

# --- 6. 执行脚本 (未修改) ---
if __name__ == "__main__":
    
    start_time = time.time()
    result_df = main(urls)
    end_time = time.time()
    
    if result_df is not None:
        output_filename = 'archdaily_projects.csv'
        result_df.to_csv(output_filename, index=False, encoding='utf-8')
        logging.info(f"\n--- Process Finished ---")
        logging.info(f"Successfully saved {len(result_df)} projects to {output_filename}")
    else:
        logging.info("\n--- Process Finished ---")
        logging.info("No data was generated.")
        
    logging.info(f"Total execution time: {end_time - start_time:.2f} seconds")