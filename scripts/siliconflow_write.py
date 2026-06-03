#!/usr/bin/env python3
"""
SiliconFlow 备用写作引擎
用法：python scripts/siliconflow_write.py <fact_sheet_file> [--model <model_id>]

从 fact_sheet_file 读取已核实的事实清单（纯文本/markdown），
结合 knowledge/wizard-style.md 与 knowledge/anti-cliche.md，
调用 SiliconFlow API 生成正文，输出到 stdout。

必须设置环境变量：
  SF_API_KEY   你的 SiliconFlow API Key

可选环境变量：
  SF_MODEL     默认 Qwen/Qwen2.5-72B-Instruct（可改为 deepseek-ai/DeepSeek-V3 等）
  SF_BASE_URL  默认 https://api.siliconflow.cn/v1
"""

import os
import sys
import json
import argparse
from pathlib import Path

# ---- 依赖检查 ----
try:
    from openai import OpenAI
except ImportError:
    print("[错误] 缺少依赖：请先运行 pip install openai", file=sys.stderr)
    sys.exit(1)

# ---- 参数 ----
parser = argparse.ArgumentParser()
parser.add_argument("fact_sheet", help="事实清单文件路径（markdown 格式）")
parser.add_argument("--model", default=None, help="指定模型 ID，覆盖 SF_MODEL 环境变量")
args = parser.parse_args()

# ---- 配置 ----
API_KEY   = os.environ.get("SF_API_KEY", "")
BASE_URL  = os.environ.get("SF_BASE_URL", "https://api.siliconflow.cn/v1")
MODEL     = args.model or os.environ.get("SF_MODEL", "Qwen/Qwen2.5-72B-Instruct")

if not API_KEY:
    print("[错误] 请设置环境变量 SF_API_KEY", file=sys.stderr)
    sys.exit(1)

# ---- 读取知识库文件 ----
root = Path(__file__).parent.parent  # ma-content/
def read_kb(rel_path):
    p = root / rel_path
    return p.read_text(encoding="utf-8") if p.exists() else ""

wizard_style  = read_kb("knowledge/wizard-style.md")
anti_cliche   = read_kb("knowledge/anti-cliche.md")
fact_sheet    = Path(args.fact_sheet).read_text(encoding="utf-8")

# ---- 构建 Prompt ----
SYSTEM = f"""你是一位专注 M&A（并购）领域的公众号主笔，文风参考巫师财经。
下面是你必须遵守的风格指南（wizard-style）：
---
{wizard_style}
---
下面是必须主动规避的套路词与 AI 腔清单（anti-cliche）：
---
{anti_cliche}
---
全程红线：
1. 绝不编造数据、情节或未公开事实；信息不足就写"未披露"。
2. 专注分析本笔交易本身，不做泛行业科普灌水。
3. 不写清单里任何一条套路词/句式。
4. 原创度目标 ≥ 90%，金句密度每 300 字 1-2 句。
5. 篇幅 1500-2500 字。
"""

USER = f"""以下是已经核实的交易事实清单（每条都有来源）：

{fact_sheet}

请基于上述事实，按 wizard-style 的叙事结构写出完整正文。
要求：
- 强钩子开场，不以公告体开头
- 每个小标题用 ### 三级标题
- 核心观点加粗
- 文末抛出一个开放式问题引导评论
- 资料来源不需要在正文里写（调用方会单独追加）

只输出正文 markdown，不加任何解释或前言。
"""

# ---- 调用 API ----
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

try:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user",   "content": USER},
        ],
        temperature=0.75,
        max_tokens=4096,
    )
    content = response.choices[0].message.content
    print(content)
except Exception as e:
    print(f"[错误] API 调用失败：{e}", file=sys.stderr)
    sys.exit(1)
