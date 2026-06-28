#!/usr/bin/env python3
"""
daily_run.py — 本地每日全流程总调度（不依赖云端）

流程：
  1. 选题（claude CLI 联网搜索 + 去重自检）→ topics/YYYY-MM-DD.md
  2. 取排名第一的选题 → 调 write_article.py 写稿 + 排版 + 推微信草稿箱
  3. 归档到本地成品库 + 追加 written-deals.md 去重登记
  4. （可选）git commit & push 作备份

用法：
  py scripts/daily_run.py                  # 完整跑
  py scripts/daily_run.py --model deepseek # 用 SiliconFlow 写稿
  py scripts/daily_run.py --skip-topics    # 跳过选题，直接用已有当天 topics
  py scripts/daily_run.py --no-wechat      # 不推微信
  py scripts/daily_run.py --git            # 跑完顺便 git push 备份
"""
import argparse, json, os, re, sys, subprocess, datetime, pathlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO     = pathlib.Path(__file__).parent.parent
TOPICS   = REPO / "topics"
SCRIPTS  = REPO / "scripts"
LEDGER   = REPO / "knowledge" / "written-deals.md"
TODAY    = datetime.date.today().isoformat()


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── 步骤1：选题（claude CLI） ─────────────────────────────────────────────────
def select_topics() -> pathlib.Path:
    topic_file = TOPICS / f"{TODAY}.md"
    if topic_file.exists():
        log(f"今日选题已存在，跳过选题：{topic_file.name}")
        return topic_file

    log("开始选题（claude CLI 联网搜索 + 去重自检）…")
    prompt = f"""你是 M&A 公众号的选题编辑。请完成今天（{TODAY}）的并购选题，并把结果用 Write 工具写入文件 `{topic_file.as_posix()}`。

## 第一步：去重自检（强制）
先用 Read 读 `{LEDGER.as_posix()}`（已写交易登记册），再用 Glob+Read 看 `{TOPICS.as_posix()}` 下最近的清单文件，建立"近期已写/已选"清单。
判重规则：候选交易的「买方+标的」或「核心标的公司」已在登记册或近 7 天 topics 中出现 → 判为重复，直接剔除，不得入选（除非有重大新进展可作跟进稿）。

## 第二步：检索选题
用 WebSearch 并行检索（时间窗 48 小时为主）：
- 中国侧：中国 并购 / A股 重组 / 中概股 私有化 / 中企 出海 / PE 控股权
- 国际侧：acquisition merger 2026 billion / 跨国公司 并购 2026 / Big Tech acquisition
必要时用 WebFetch 核实原文。任何数字/对价/日期必须有来源链接。

## 第三步：筛选与排序
去重、过滤，按热度/独特性/可分析度/时效性/共鸣度五维打分，筛 6-10 条，中国:国际约 70:30。

## 第四步：写入文件
用 Write 写入 `{topic_file.as_posix()}`，格式：
- 首行标题 `# M&A 选题清单 · {TODAY}`
- 一段汇总说明（共筛出几条、今日最值得写第几条）
- `## 汇总表`，markdown 表格，表头：`| # | 选题 | 推荐 | 轨道 | 交易日期 | 一句话钩子 |`（推荐用★1-5星）
- `## 详细条目`，每条 `### N. 标题`，含：一句话看点、拟切入角度、关键交易要素、来源链接、推荐指数、`去重核对：✅ 未与登记册/近7天topics重复`

只输出进度说明，文件用 Write 工具落盘。完成后回复"选题完成"。"""

    cmd = ["claude", "-p", prompt,
           "--allowed-tools", "WebSearch", "WebFetch", "Read", "Write", "Glob", "Grep"]
    r = subprocess.run(cmd, cwd=REPO, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if not topic_file.exists():
        raise RuntimeError("选题失败：claude 未生成 topics 文件")
    log(f"选题完成：{topic_file}")
    return topic_file


# ── 步骤2：解析排名第一的选题 ─────────────────────────────────────────────────
def parse_top_topic(topic_file: pathlib.Path) -> dict:
    text = topic_file.read_text(encoding="utf-8")
    # 优先从汇总表取第 1 行
    row = re.search(r"^\|\s*1\s*\|\s*(.+?)\s*\|\s*([★☆]+)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
                    text, re.M)
    if row:
        return {"rank": 1, "title": row.group(1).strip(),
                "track": row.group(3).strip(), "hook": row.group(5).strip(),
                "date": TODAY}
    # 兜底：详细条目第 1 条
    sec = re.search(r"###\s*1[\.、]\s*(.+)", text)
    title = sec.group(1).strip() if sec else "今日并购选题"
    hook = re.search(r"一句话看点[：:]\*?\*?(.+?)\*?\*?(?:\n|$)", text)
    return {"rank": 1, "title": title,
            "hook": hook.group(1).strip() if hook else "", "track": "", "date": TODAY}


# ── 步骤3：写稿（调 write_article.py） ────────────────────────────────────────
def write_article(topic: dict, model: str, no_wechat: bool) -> pathlib.Path | None:
    log(f"开始写稿：{topic['title'][:30]}（模型 {model}）")
    cmd = [sys.executable, str(SCRIPTS / "write_article.py"),
           "--topic", json.dumps(topic, ensure_ascii=False), "--model", model]
    if no_wechat:
        cmd.append("--no-push")
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    print(r.stdout)
    if r.returncode != 0:
        log(f"写稿失败：{r.stderr[:300]}")
        return None
    m = re.search(r"DRAFT_PATH:(.+)", r.stdout)
    return pathlib.Path(m.group(1).strip()) if m else None


# ── 步骤4：本地归档 + 登记去重库 ──────────────────────────────────────────────
def archive_and_register(topic: dict, draft_path: pathlib.Path):
    log("本地归档 + 登记去重库…")
    # 复用 sync_local 的散落拼装逻辑
    subprocess.run([sys.executable, str(SCRIPTS / "sync_local.py"), "--no-pull"],
                   cwd=REPO, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    # 追加登记册
    if draft_path and LEDGER.exists():
        title = topic["title"]
        try:
            first = draft_path.read_text(encoding="utf-8").splitlines()[0]
            title = first.lstrip("# ").strip() or title
        except Exception:
            pass
        rel = draft_path.relative_to(REPO).as_posix()
        row = f"| {TODAY} | {topic.get('buyer','')} | {topic.get('target','')} | {topic.get('track','')} | {title} | {rel} |\n"
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(row)
        log("已追加去重登记册")


# ── 步骤3.5：生成小红书图文 + 公众号封面对 ────────────────────────────────────
def make_cards(draft_path: pathlib.Path):
    if not draft_path:
        return
    log("生成小红书图文 + 公众号封面对…")
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "make_cards.py"), "--draft", str(draft_path)],
        cwd=REPO, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode != 0:
        log("配图生成失败（不影响其他产物）")


# ── 步骤3.6：推微信草稿箱（用今天的封面图） ──────────────────────────────────
def push_wechat_with_cover(draft_path: pathlib.Path):
    log("推送微信草稿箱…")
    slug_dir = draft_path.stem
    cards = REPO / "social-cards" / slug_dir
    # 封面优先级：21:9 头图 > 1:1 分享卡 > 小红书封面
    cover = None
    for pat in ("wechat/output/wechat-wide.png",
                "wechat/output/wechat-square.png",
                "xhs/output/xhs-01.png"):
        p = cards / pat
        if p.exists():
            cover = p; break
    # 排版 html
    html = pathlib.Path.home() / "wechat-out" / slug_dir / "article.html"
    push = SCRIPTS / "push_draft.py"
    cmd = [sys.executable, str(push)]
    if html.exists():
        cmd += ["--html", str(html)]
    cmd.append(str(draft_path))
    if cover:
        cmd.append(str(cover))
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    print(r.stdout.strip())
    if r.returncode != 0:
        log(f"微信推送失败：{r.stderr[:200]}")


# ── 可选：git 备份 ────────────────────────────────────────────────────────────
def git_backup():
    log("git 提交并推送备份…")
    subprocess.run(["git", "add", "-A"], cwd=REPO)
    subprocess.run(["git", "commit", "-m", f"本地每日产出:{TODAY}"], cwd=REPO,
                   capture_output=True)
    r = subprocess.run(["git", "push"], cwd=REPO, capture_output=True, text=True)
    log("git push 成功" if r.returncode == 0 else f"git push 失败（不影响本地）：{r.stderr[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet")
    ap.add_argument("--skip-topics", action="store_true")
    ap.add_argument("--no-wechat", action="store_true")
    ap.add_argument("--no-cards", action="store_true", help="跳过小红书图/封面")
    ap.add_argument("--git", action="store_true")
    args = ap.parse_args()

    log(f"===== 本地每日流程开始 {TODAY} =====")
    topic_file = (TOPICS / f"{TODAY}.md") if args.skip_topics else select_topics()
    if not topic_file.exists():
        log("无 topics 文件，终止"); sys.exit(1)

    topic = parse_top_topic(topic_file)
    log(f"今日第一条：{topic['title']}")

    # 写稿+排版（先不推微信，等配图出封面后再推）
    draft = write_article(topic, args.model, no_wechat=True)
    if draft:
        log(f"成稿：{draft}")
    # 配图：小红书5图 + 公众号封面对（封面供微信推送使用）
    if draft and not args.no_cards:
        make_cards(draft)
    # 推微信草稿箱（用今天生成的 21:9 头图作封面）
    if draft and not args.no_wechat:
        push_wechat_with_cover(draft)
    archive_and_register(topic, draft)

    if args.git:
        git_backup()

    log(f"===== 完成。成品库：~/Documents/M&A归档 =====")


if __name__ == "__main__":
    main()
