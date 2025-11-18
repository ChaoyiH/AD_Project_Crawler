import os
import json
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

# 定义文件路径
base_dir = "data/902420"
input_file = os.path.join(base_dir, "902420_details.json")
output_file = os.path.join(base_dir, "902420.json")

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
        result_text = response.text
        
        # 清理可能存在的 Markdown 标记
        if result_text.startswith("```json"):
            result_text = result_text.replace("```json", "").replace("```", "")
        elif result_text.startswith("```"):
            result_text = result_text.replace("```", "")
            
        return json.loads(result_text.strip())
    except Exception as e:
        print(f"调用 API 或解析 JSON 时出错: {e}")
        return {}

def main():
    # 2. 读取原始 JSON 文件
    if not os.path.exists(input_file):
        print(f"找不到文件: {input_file}")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        original_data = json.load(f)

    print(f"正在处理项目: {original_data.get('Project Title', 'Unknown')}")

    # 准备 Description 文本 (原本是列表，转为字符串)
    description_list = original_data.get("Description", [])
    description_str = "\n".join(description_list) if isinstance(description_list, list) else str(description_list)
    print(description_str)
    # 3. 调用 AI 提取信息
    extracted_data = extract_info_with_gemini(description_str)

    if not extracted_data:
        print("未能提取到数据，终止操作。")
        return

    # 4. 合并数据 (原始数据 + 提取的数据)
    # 使用 update 方法，保留原始键值，添加新键值
    original_data.update(extracted_data)

    # 5. 保存到新文件
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(original_data, f, ensure_ascii=False, indent=2)

    print(f"处理完成！文件已保存至: {output_file}")
    print("提取的键值示例:")
    for key in extracted_data.keys():
        print(f"- {key}")

if __name__ == "__main__":
    main()