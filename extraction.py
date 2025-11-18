import os
import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import google.generativeai as genai


proxy_url = "http://127.0.0.1:7890"  

os.environ['HTTP_PROXY'] = proxy_url
os.environ['HTTPS_PROXY'] = proxy_url


# 1. 配置 API
# 假设 GEMINI_API_KEY 已经在环境变量中
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    raise ValueError("错误：未找到 GEMINI_API_KEY 环境变量，请先配置。")

genai.configure(api_key=api_key)

# 根数据目录（遍历该目录下的所有子文件夹）
DATA_ROOT = "data"

def extract_info_with_gemini(description_text):
    """
    调用 Gemini API 提取结构化信息
    """
    # 使用 gemini-2.5-flash 模型，速度快且适合此类提取任务
    model = genai.GenerativeModel('gemini-2.5-flash')

    prompt = f"""
    你是一个建筑数据结构化专家。请阅读以下项目描述，提取特定信息并以严格的 JSON 格式返回。
    请尽量保留原始文本中的措辞。如果某项信息在描述中未提及，请将该键的值设为空字符串 ""。

    需要提取的 9 个字段如下：
    1. "设计理念"
    2. "陈列展览区"
    3. "公共服务区"
    4. "业务科研用房"
    5. "藏品库区"
    6. "综合大厅/中庭"
    7. "特效影厅"
    8. "科教活动"
    9. "建筑形态特征"

    描述文本：
    {description_text}

    请只返回 JSON 字符串，不要包含 Markdown 格式标记（如 ```json）。
    """

    try:
        response = model.generate_content(prompt)
        result_text = response.text or "{}"

        # 清理可能存在的 Markdown 标记
        if result_text.startswith("```json"):
            result_text = result_text.replace("```json", "").replace("```", "")
        elif result_text.startswith("```"):
            result_text = result_text.replace("```", "")

        result_text = result_text.strip()
        # 确保是可解析的 JSON
        return json.loads(result_text if result_text else "{}")
    except Exception as e:
        print(f"调用 API 或解析 JSON 时出错: {e}")
        traceback.print_exc()
        return {}


def process_project_dir(project_dir):
    """
    处理单个项目目录：读取 `<folder>_details.json`，调用提取，生成 `<folder>.json`。
    """
    try:
        folder_name = os.path.basename(os.path.normpath(project_dir))
        input_file = os.path.join(project_dir, f"{folder_name}_details.json")
        output_file = os.path.join(project_dir, f"{folder_name}.json")

        if not os.path.exists(input_file):
            return f"跳过：缺少文件 {input_file}"

        with open(input_file, 'r', encoding='utf-8') as f:
            original_data = json.load(f)

        # 准备 Description 文本 (原本可能是列表)
        description_list = original_data.get("Description", [])
        if isinstance(description_list, list):
            description_str = "\n".join([str(x) for x in description_list])
        else:
            description_str = str(description_list)

        extracted_data = extract_info_with_gemini(description_str)

        if not extracted_data:
            return f"失败：未能提取数据 {folder_name}"

        # 合并数据
        original_data.update(extracted_data)

        # 保存
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(original_data, f, ensure_ascii=False, indent=2)

        return f"完成：{folder_name} -> {output_file}"
    except Exception as e:
        traceback.print_exc()
        return f"错误：处理 {project_dir} 时出错：{e}"

def main():
    if not os.path.isdir(DATA_ROOT):
        print(f"未找到数据目录：{DATA_ROOT}")
        return

    # 收集所有一级子目录
    project_dirs = [
        entry.path
        for entry in os.scandir(DATA_ROOT)
        if entry.is_dir()
    ]

    if not project_dirs:
        print("未找到任何项目子文件夹。")
        return

    print(f"发现 {len(project_dirs)} 个项目，将使用 5 线程处理……")

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_dir = {executor.submit(process_project_dir, d): d for d in project_dirs}
        for future in as_completed(future_to_dir):
            msg = future.result()
            results.append(msg)
            print(msg)

    # 汇总
    success = sum(1 for r in results if r.startswith("完成："))
    skipped = sum(1 for r in results if r.startswith("跳过："))
    failed = sum(1 for r in results if r.startswith("失败：") or r.startswith("错误："))
    print("——— 汇总 ———")
    print(f"成功：{success}，跳过：{skipped}，失败：{failed}")

if __name__ == "__main__":
    main()