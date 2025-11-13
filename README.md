# ArchDaily Project Crawler 🕷️

这是一个功能强大的 ArchDaily 项目自动化爬虫工具。它能够根据特定关键词生成项目列表，并深度抓取每个项目的详细元数据、完整文本描述以及高清图库。

本项目经过专门优化，能够有效处理 ArchDaily 的**动态加载内容 (AJAX)**，并通过 **Cookie 注入** 技术绕过反爬限制，确保抓取到完整的项目描述文本。

## ✨ 主要功能

* **智能列表生成 (`list_generator.py`)**
    * 基于预设关键词（如 Science, Museum, Technology 等）自动搜索项目。
    * 内置智能过滤器，自动剔除无关项目（如 Art Museums, Research Labs 等），确保数据纯度。
    * 生成去重后的 CSV 任务列表。
* **深度数据抓取 (`AD_crawler.py`)**
    * **完整文本抓取**: 使用 Selenium + Cookie 注入，完美解决 ArchDaily 正文懒加载导致抓取不全的问题。
    * **高清图片下载**: 自动解析图库，下载高清大图并保存元数据（文件名、标签、说明）。
    * **元数据提取**: 提取项目 ID、标题、建筑师、面积、年份、地点、分类等结构化数据。
* **灵活的运行模式**
    * **断点续传**: 通过 CSV 状态列 (`downloaded`, `error`, `incomplete`) 自动管理进度，支持中断后继续。
    * **文本专用模式 (`-t`)**: 支持跳过耗时的图片下载，仅快速修复或更新文本信息。
    * **调试模式 (`-d`)**: 单次运行测试，不污染数据文件。

## 🛠️ 环境准备

### 1. 安装依赖
确保已安装 Python 3.8+ 和 Google Chrome 浏览器。
安装所需的 Python 库：

```bash
pip install selenium requests beautifulsoup4 pandas webdriver-manager