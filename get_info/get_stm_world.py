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
You are a professional International Museum Data Specialist. Your task is to precisely collect detailed information about a specific science museum based on the provided name, using Google Search, and output it strictly in the specified JSON format.

# Task
Target Object: {museum_name}
Target Actions:
1. Search primarily in **English** (or the local language of the museum) using sources like the Official Website, Wikipedia, TripAdvisor, and ArchDaily.
2. Extract factual data.
3. Output strictly in JSON format.

Core Principle: Fact-first. No hallucinations. If a specific field cannot be verified with certainty, fill it with `null`. Do not invent numbers or details.

# Output Schema (JSON)
Please strictly follow the JSON keys below. Do not modify the key names.

```json
{{
    "name": "{museum_name}",
    "opening_date": "Format: YYYY-MM-DD (Precise to the day; if unavailable, use YYYY-MM). If unknown, fill `null`.",
    "total_construction_area": "String with number and unit. E.g., '102,000 sqm' or '500,000 sq ft'. If unknown, fill `null`.",
    "floors_above_ground": "Number (floors above ground) only. If unknown, fill `null`.",
    "floors_under_ground": "Number (floors below ground) only. If unknown, fill `null`.",
    "building_height": "String with number and unit. E.g., '45 m' or '150 ft'. If unknown, fill `null`.",
    "concept&appearance": "Detailed description including: 1. Design concept/philosophy; 2. Exterior appearance/shape; 3. Relationship with the surrounding environment. If unknown, fill `null`.",
    "permanent_exhibitions": "Detailed description. List all core permanent exhibition halls/galleries. For each, describe the theme, key exhibits/artifacts, and educational significance. Keep rich textual details; do not summarize overly briefly. If unknown, fill `null`.",
    "central_hall": "Description of the physical characteristics of the main lobby/atrium (e.g., ceiling height, area, iconic installations). If unknown, fill `null`.",
    "special_effects_theaters": "List all special effect theaters (e.g., IMAX, Dome/Planetarium, 4D, Motion Ride, etc.) and briefly describe their screen size or technical features. If unknown, fill `null`.",
    "science_popularization_activities": "Describe the museum's featured educational programs, science shows, lectures, or school partnership programs. If unknown, fill `null`."
}}
```
# Output Requirements
1. Output strictly valid JSON (UTF-8). Do not wrap in markdown code blocks (no triple backticks).
2. Do not add or delete any Keys.
3. If a numeric field cannot be verified, fill null.
4. Language: The content values must be in English.
5. Do not speculate. Only provide information backed by search results.
6. If real-time web access fails, rely only on known authoritative public data; otherwise, fill null.
"""

CSV_PATH = Path("world_stm.csv")
OUTPUT_DIR = Path("output")
MODEL_NAME = "gemini-3-pro-preview"
MAX_QPM = 20  # hard limit to keep qpm < 20
MAX_WORKERS = 20


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
