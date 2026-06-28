#!/usr/bin/env python3
"""
write_article.py — 根据选题话题调用 AI 写稿，然后排版并推送草稿箱
用法: python scripts/write_article.py --topic '<json>' --model <model_key>

输出: 写入 drafts/YYYY-MM-DD-slug.md，最后打印 DRAFT_PATH:<path>
"""
import argparse, json, os, re, sys, pathlib, subprocess, datetime, unicodedata

REPO      = pathlib.Path(__file__).parent.parent
SECRETS   = pathlib.Path.home() / ".ma-secrets"
TEMPLATE  = REPO / "templates" / "article-template.md"
DRAFTS    = REPO / "drafts"
WECHAT_OUT = pathlib.Path.home() / "wechat-out"
FMT_REPO  = pathlib.Path.home() / "xiaohu-fmt"

# ── 模型配置 ──────────────────────────────────────────────────────────────────
MODEL_MAP = {
    "claude-sonnet": {"model": "claude-sonnet-4-5", "provider": "claude-cli"},
    "claude-opus":   {"model": "claude-opus-4-5",   "provider": "claude-cli"},
    "deepseek":      {"model": "deepseek-ai/DeepSeek-V3", "provider": "siliconflow"},
}

def get_sf_key() -> str:
    key = os.environ.get("SF_API_KEY", "")
    if key:
        return key
    sf_file = SECRETS / "siliconflow.json"
    if sf_file.exists():
        return json.loads(sf_file.read_text())["api_key"]
    raise RuntimeError("未找到 SiliconFlow API Key，请设置环境变量 SF_API_KEY")

def slugify(title: str) -> str:
    """中文标题 → 适合文件名的 slug"""
    # 去掉标点和特殊字符
    title = re.sub(r"[^\w一-鿿]+", "-", title)
    return title.strip("-")[:40]

# ── 读取文章模板 ──────────────────────────────────────────────────────────────
def get_template() -> str:
    if TEMPLATE.exists():
        return TEMPLATE.read_text(encoding="utf-8")
    return """# [文章标题]

[开头2-3段，直接切入，不加小节标题]

---

### [第一个小节标题]

[内容]

---

### [结尾]

[回扣开头 + 开放式问题]

---

> **资料来源**
> 1. [来源1]
"""

# ── 构建写作 prompt ───────────────────────────────────────────────────────────
def build_prompt(topic: dict, template: str) -> str:
    return f"""你是一位专业的并购财经公众号作者，文风犀利、逻辑严谨、有洞察力。

## 本次写作任务

请就以下选题，先用 WebSearch / WebFetch 采集真实事实，再撰写一篇完整的微信公众号文章。

### 选题信息
- **标题/话题**: {topic.get("title", "")}
- **核心看点**: {topic.get("hook", "")}
- **赛道**: {topic.get("track", "")}
- **日期**: {topic.get("date", "")}

### 第一步：事实采集（强制）
用 WebSearch/WebFetch 检索这笔交易的要素：买方/卖方/标的、对价与支付方式、估值溢价、时间线、监管、双方表态。每个关键数字都要有来源链接。
**重要**：若多方检索后仍无法证实该交易真实存在，不要写免责声明，而是改写这一并购领域当下确有其事、最接近的真实交易；并在文末资料来源注明实际所写交易。绝不编造数据。

### 第二步：写作 + 排版规范（必须严格遵守）
1. `#` 只用于文章标题（首行），`###` 用于各节标题，**禁止使用 `##`**
2. 开头 2-3 段直接切入，**不加节标题**，营造悬念感
3. 每个节之间用 `---` 分隔（必须）
4. **加粗**仅用于判断性金句，全文不超过 6 处
5. 结尾：回扣开头 + 开放式问题 + `---` + `> **资料来源**`（编号列表，附真实链接）
6. 禁止在结尾加 `### 结尾` 或类似标题
7. 全文 **1500-2500 字**，巫师财经风格：强钩子开场、聚焦分析本交易、长短句交错、2-4个新鲜比喻、2-4句金句

### 输出要求（关键）
- **把最终文章用 Write 工具写入文件**：`{{OUTPUT_PATH}}`
- 文件第一行必须是 `# 文章标题`（你来拟一个吸引眼球的标题）
- 不要在对话里输出正文，只在完成后回复"写作完成"
"""

# ── 调用 claude CLI（联网研究 + 自行 Write 落盘） ────────────────────────────
def call_claude_cli(prompt: str, model: str, out_path: pathlib.Path):
    import shutil
    if not shutil.which("claude"):
        raise RuntimeError("claude CLI 未找到，请确保 Claude Code 已安装")
    prompt = prompt.replace("{OUTPUT_PATH}", out_path.as_posix())
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model,
         "--allowed-tools", "WebSearch", "WebFetch", "Read", "Write", "Glob", "Grep"],
        text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=600
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI 失败（退出码 {result.returncode}）")
    if not out_path.exists():
        raise RuntimeError("claude CLI 未生成稿件文件")

# ── 调用 SiliconFlow API ─────────────────────────────────────────────────────
def call_siliconflow(prompt: str, model: str, api_key: str) -> str:
    import urllib.request
    body = json.dumps({
        "model": model, "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.siliconflow.cn/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]

# ── 排版（xiaohu-wechat-format）────────────────────────────────────────────
def format_article(draft_path: pathlib.Path) -> pathlib.Path | None:
    if not FMT_REPO.exists():
        print("排版工具未安装，跳过排版步骤")
        return None
    config = {
        "output_dir": str(WECHAT_OUT),
        "vault_root": str(REPO),
        "image_search_paths": [str(REPO / "assets")],
        "settings": {"default_theme": "bytedance", "auto_open_browser": False},
        "wechat": {}, "cover": {}, "ai": {}
    }
    cfg_path = FMT_REPO / "config.json"
    cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    WECHAT_OUT.mkdir(parents=True, exist_ok=True)
    script = FMT_REPO / "scripts" / "format.py"
    result = subprocess.run(
        [sys.executable, str(script),
         "--input", str(draft_path), "--theme", "bytedance",
         "--output", str(WECHAT_OUT)],
        capture_output=True, text=True, encoding="utf-8"
    )
    # format.py 会把结果输出到 wechat-out/<文件名>/article.html（子文件夹），
    # 也兼容直接输出到 wechat-out/article.html 的情况。取最新的那个。
    candidates = list(WECHAT_OUT.glob("article.html")) + list(WECHAT_OUT.glob("*/article.html"))
    if candidates:
        html = max(candidates, key=lambda p: p.stat().st_mtime)
        print(f"排版完成: {html}")
        return html
    print(f"排版警告: 未找到 article.html。stderr={result.stderr[:200]}")
    return None

# ── 推送微信草稿箱 ────────────────────────────────────────────────────────────
def push_to_wechat(draft_path: pathlib.Path, html_path: pathlib.Path | None):
    push_script = REPO / "scripts" / "push_draft.py"
    if not push_script.exists():
        print("push_draft.py 不存在，跳过推送")
        return
    if html_path and html_path.exists():
        cmd = [sys.executable, str(push_script), "--html", str(html_path), str(draft_path)]
    else:
        cmd = [sys.executable, str(push_script), str(draft_path)]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    print(result.stdout)
    if result.returncode != 0:
        print(f"推送错误: {result.stderr[:300]}")

# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True, help="话题 JSON 字符串")
    parser.add_argument("--model", default="claude-sonnet")
    parser.add_argument("--no-format", action="store_true", help="跳过排版")
    parser.add_argument("--no-push",   action="store_true", help="跳过推送")
    args = parser.parse_args()

    topic = json.loads(args.topic)
    model_cfg = MODEL_MAP.get(args.model, MODEL_MAP["claude-sonnet"])
    provider  = model_cfg["provider"]

    print(f"模型: {model_cfg['model']} ({provider})")
    print(f"话题: {topic.get('title', '')}")

    # 构建 prompt
    template = get_template()
    prompt = build_prompt(topic, template)

    # 临时输出路径（基于选题标题，写完后按文章实际标题重命名）
    date = topic.get("date") or datetime.date.today().isoformat()
    DRAFTS.mkdir(parents=True, exist_ok=True)
    tmp_path = DRAFTS / f"{date}-{slugify(topic.get('title','article'))}.md"

    # 调用 AI 写稿
    print("正在调用 AI 写稿（含联网研究），请稍候…")
    if provider == "claude-cli":
        call_claude_cli(prompt, model_cfg["model"], tmp_path)
        content = tmp_path.read_text(encoding="utf-8")
    elif provider == "siliconflow":
        api_key = get_sf_key()
        print("SiliconFlow Key 已就绪")
        content = call_siliconflow(prompt, model_cfg["model"], api_key)
        tmp_path.write_text(content, encoding="utf-8")
    else:
        raise RuntimeError(f"未知 provider: {provider}")

    print(f"AI 写稿完成，字数: {len(content)}")

    # 按文章实际 H1 标题重命名
    title_line = re.search(r"^# (.+)$", content, re.M)
    title = title_line.group(1).strip() if title_line else topic.get("title", "article")
    draft_path = DRAFTS / f"{date}-{slugify(title)}.md"
    if draft_path != tmp_path:
        if draft_path.exists():
            draft_path = DRAFTS / f"{date}-{slugify(title)}-2.md"
        tmp_path.rename(draft_path)
    print(f"草稿已保存: {draft_path}")
    print(f"DRAFT_PATH:{draft_path}")  # 供仪表盘捕获

    # 排版
    html_path = None
    if not args.no_format:
        html_path = format_article(draft_path)

    # 推送草稿箱
    if not args.no_push:
        push_to_wechat(draft_path, html_path)

    print("全部完成！")

if __name__ == "__main__":
    main()
