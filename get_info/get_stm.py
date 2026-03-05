import csv
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

from google import genai
from google.genai import types


PROMPT_TEMPLATE = """# Role
你是一名专业的博物馆数据采集专家。你的任务是根据用户提供的【科技馆名称】，利用互联网搜索工具，精准采集该场馆的详细信息，并严格按照指定的JSON格式输出。

# Task
目标对象：{museum_name}
目标动作：搜索官方网站、百度百科、携程/去哪儿景点介绍、建筑设计类网站。
核心原则：事实优先，拒绝幻觉。如果某个字段实在搜索不到确切数据，请填入 `null`，严禁编造数值。

# Output Schema (JSON)
请严格按照以下JSON Key进行填充，不要修改Key的名称：

```json
{{
    "name": "请填入标准中文名称",
    "english_name": "请填入官方英文名称，若未知填 null",
    "opening_date": "格式：YYYY年MM月DD日（精确到日，若无法精确到日则到月）若未知填 null",
    "total_construction_area_sqm": "仅填数字（单位：平方米），例如：102000，若未知填 null",
    "floors_above_ground": "仅填数字（地上层数），若未知填 null",
    "floors_under_ground": "仅填数字（地下层数），若未知填 null",
    "building_height_meters": "仅填数字（建筑高度），若未知填 null",
    "concept&appearance": "详细描述。需包含：1. 设计理念；2. 外观造型描述；3. 建筑与周边环境的关系。若未知填 null",
    "permanent_exhibitions": "详细描述。请列出所有核心常设展厅的名称，并详细介绍每个展厅的主题、核心展品及教育意义。请保留丰富的文本细节，不要过度概括，字数不限。若未知填 null",
    "central_hall": "描述序厅/中庭的物理空间特征（如挑高、面积）。若未知填 null",
    "special_effects_theaters": "列出馆内所有的特效影院类型（如IMAX、球幕、4D、巨幕、动感影院等），并简述其屏幕尺寸或技术特点。若未知填 null",
    "science_popularization_activities": "描述该馆特色的科普教育品牌活动、科学实验表演、科普讲座或馆校合作项目。若未知填 null"
}}
```

输出要求：
1. 只输出严格 JSON（UTF-8），不添加反引号、不添加说明文字。
2. 不得新增或删除任何 Key。
3. 若无法查证的数值字段填 null；若总建筑面积为区间或不精确，尝试找最可信的官方或权威来源单值；仍无则 null。
4. language: 中文。
5. 禁止臆测或生成看似合理但无法证实的信息。
6. 若模型无法访问实时网页，请仅基于已知权威公开资料并对未知填 null。
"""

CSV_PATH = Path("china_stm.csv")
OUTPUT_DIR = Path("output")
MODEL_NAME = "gemini-3-pro-preview"
MAX_QPM = 19  # hard limit to keep qpm < 20
MAX_WORKERS = 5


class RateLimiter:
    """Thread-safe rate limiter based on a fixed minimum interval."""

    def __init__(self, max_per_minute: int) -> None:
        if max_per_minute <= 0:
            raise ValueError("max_per_minute must be positive")
        self._min_interval = 60.0 / max_per_minute
        self._lock = threading.Lock()
        self._next_time = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_time = max(0.0, self._next_time - now)
            self._next_time = max(now, self._next_time) + self._min_interval
        if wait_time:
            time.sleep(wait_time)


def sanitize_filename(name: str) -> str:
    """Replace characters that are invalid for Windows filenames."""

    cleaned = re.sub(r"[\\/:*?\"<>|]", "_", name.strip())
    return cleaned or "output"


def read_csv_rows(path: Path) -> Tuple[List[Dict[str, str]], List[str], str]:
    errors = []
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as infile:
                reader = csv.DictReader(infile)
                rows = list(reader)
                if reader.fieldnames is None:
                    raise ValueError("CSV 缺少表头")
                return rows, reader.fieldnames, encoding
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError(f"无法解码 CSV：{' | '.join(errors)}")


def write_csv_rows(path: Path, fieldnames: List[str], rows: List[Dict[str, str]], encoding: str) -> None:
    with path.open("w", encoding=encoding, newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_contents(museum_name: str) -> List[types.Content]:
    prompt_text = PROMPT_TEMPLATE.format(museum_name=museum_name)
    return [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_text)],
        )
    ]


def extract_json_payload(response_text: str) -> Dict:
    text = (response_text or "").strip()
    if not text:
        raise ValueError("模型未返回任何内容")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("未找到 JSON 内容，请检查模型输出")

    json_text = text[start : end + 1]
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型返回的 JSON 无法解析: {exc}") from exc


def fetch_museum(row: Dict[str, str], client: genai.Client, config: types.GenerateContentConfig, rate_limiter: RateLimiter) -> Tuple[str, bool, str]:
    museum_name = (row.get("name") or "").strip()
    row_id = row.get("index", museum_name)
    if not museum_name:
        return row_id, False, "name 列为空"

    rate_limiter.acquire()
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=build_contents(museum_name),
            config=config,
        )
        payload = extract_json_payload(response.text)
    except Exception as exc:  # broad catch to keep worker alive
        return row_id, False, str(exc)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_name = sanitize_filename(museum_name) + ".json"
    output_path = OUTPUT_DIR / file_name
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return row_id, True, str(output_path)


def generate_batch() -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("请先设置 GEMINI_API_KEY 环境变量")

    rows, fieldnames, encoding = read_csv_rows(CSV_PATH)
    pending_rows = [row for row in rows if (row.get("status") or "").strip() != "1"]
    if not pending_rows:
        print("无需处理，所有场馆已完成")
        return

    client = genai.Client(api_key=api_key)
    rate_limiter = RateLimiter(MAX_QPM)
    tools = [types.Tool(googleSearch=types.GoogleSearch())]
    config = types.GenerateContentConfig(tools=tools)

    success_ids = set()
    failed: List[Tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pending_rows))) as executor:
        future_map = {
            executor.submit(fetch_museum, row, client, config, rate_limiter): row
            for row in pending_rows
        }
        for future in as_completed(future_map):
            row = future_map[future]
            row_id, ok, info = future.result()
            if ok:
                row["status"] = "1"
                success_ids.add(row_id)
                print(f"✅ {row_id}: {info}")
            else:
                failed.append((row_id, info))
                print(f"❌ {row_id}: {info}")

    write_csv_rows(CSV_PATH, fieldnames, rows, encoding)

    print(f"完成 {len(success_ids)} 条，失败 {len(failed)} 条")
    if failed:
        for row_id, message in failed:
            print(f"- {row_id}: {message}")


if __name__ == "__main__":
    generate_batch()
