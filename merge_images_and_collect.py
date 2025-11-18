import os
import json
import shutil
from typing import Any


def load_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def dump_json(path: str, data: Any) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_root = os.path.join(base_dir, 'data')
    out_root = os.path.join(base_dir, 'jsons')

    if not os.path.isdir(data_root):
        print(f"未找到数据目录: {data_root}")
        return

    ensure_dir(out_root)

    total = 0
    updated = 0
    created = 0
    skipped_images_missing = 0
    copied = 0

    # 遍历 data 下的所有一级子文件夹
    for entry in os.scandir(data_root):
        if not entry.is_dir():
            continue

        folder = entry.name
        proj_dir = entry.path
        images_path = os.path.join(proj_dir, f"{folder}_images.json")
        json_path = os.path.join(proj_dir, f"{folder}.json")

        total += 1

        if not os.path.exists(images_path):
            print(f"跳过(无 images)：{images_path}")
            skipped_images_missing += 1
            # 即便没有 images，也尝试复制现有 json 到汇总目录
            if os.path.exists(json_path):
                dest = os.path.join(out_root, f"{folder}.json")
                shutil.copy2(json_path, dest)
                copied += 1
            continue

        try:
            images_content = load_json(images_path)
        except Exception as e:
            print(f"读取失败：{images_path} -> {e}")
            # 也尝试复制现有 json
            if os.path.exists(json_path):
                dest = os.path.join(out_root, f"{folder}.json")
                shutil.copy2(json_path, dest)
                copied += 1
            continue

        # 期望是一个 JSON 列表
        if not isinstance(images_content, list):
            print(f"警告：{images_path} 内容非列表，已按空列表处理。")
            images_content = []

        # 读取或初始化 <id>.json
        data_obj = {}
        existed = False
        if os.path.exists(json_path):
            try:
                obj = load_json(json_path)
                if isinstance(obj, dict):
                    data_obj = obj
                    existed = True
                else:
                    print(f"警告：{json_path} 非对象，已覆盖为对象。")
            except Exception as e:
                print(f"读取失败：{json_path} -> {e}，将重建文件。")

        # 写入 images 字段
        data_obj["images"] = images_content

        # 保存回子文件夹中的 <id>.json
        try:
            dump_json(json_path, data_obj)
            if existed:
                updated += 1
            else:
                created += 1
        except Exception as e:
            print(f"写入失败：{json_path} -> {e}")
            continue

        # 复制一份到项目根目录 jsons/
        try:
            dest = os.path.join(out_root, f"{folder}.json")
            shutil.copy2(json_path, dest)
            copied += 1
        except Exception as e:
            print(f"复制失败到 {out_root}：{e}")

    print("——— 汇总 ———")
    print(f"子项目总数：{total}")
    print(f"更新现有文件：{updated}")
    print(f"新建文件：{created}")
    print(f"缺少 images 跳过：{skipped_images_missing}")
    print(f"复制到 jsons：{copied}")


if __name__ == "__main__":
    main()
