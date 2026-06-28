#!/usr/bin/env python3
"""
sync_local.py — 把云端跑完并推送到 GitHub 的每日产物，拉取到本地并分门别类归档。

流程：
  1. git pull（拿到云端当天 push 的 topics/drafts/social-cards/archive）
  2. 把每个 archive/DATE-SLUG/ 镜像到本地归档库，按「年月 / 日期-标题 / 分类子目录」整理
  3. 兜底：若某天没有 archive/ 目录（老数据），直接从 topics/drafts/social-cards 拼装

本地归档库结构：
  <LOCAL_ROOT>/
    2026-06/
      2026-06-07-某交易标题/
        01-选题.md
        02-终稿.md
        03-公众号排版.html
        04-小红书图/ xhs-01..05.png
        05-公众号封面/ wechat-wide.png, wechat-square.png
        meta.json

用法：
  py scripts/sync_local.py              # 默认归档到 ~/Documents/M&A归档
  py scripts/sync_local.py --root D:/某目录
  py scripts/sync_local.py --no-pull    # 跳过 git pull，仅重新整理
"""
import argparse, json, re, shutil, subprocess, sys, datetime
import pathlib

# Windows GBK 控制台兼容：强制 stdout 用 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO       = pathlib.Path(__file__).parent.parent
ARCHIVE    = REPO / "archive"
TOPICS     = REPO / "topics"
DRAFTS     = REPO / "drafts"
CARDS      = REPO / "social-cards"
WECHAT_OUT = pathlib.Path.home() / "wechat-out"
DEFAULT_ROOT = pathlib.Path.home() / "Documents" / "M&A归档"


def run(cmd, cwd=REPO):
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        print(f"[警告] 命令失败: {r.stderr.strip()[:300]}")
    return r.returncode == 0


def git_pull():
    print("\n── 步骤 1：从 GitHub 拉取云端产物 ──")
    run(["git", "fetch", "origin"])
    # 优先 ff-only，避免和本地未提交改动冲突
    if not run(["git", "pull", "--ff-only", "origin", "main"]):
        print("[提示] 快进失败（本地可能有改动），尝试 stash 后再 pull")
        run(["git", "stash"])
        run(["git", "pull", "--ff-only", "origin", "main"])
        run(["git", "stash", "pop"])


def title_of(md_path: pathlib.Path) -> str:
    """取 markdown 第一行 # / ## 标题"""
    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#+\s+(.+)", line)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return md_path.stem


def safe_name(s: str) -> str:
    """清洗成合法 Windows 文件夹名"""
    s = re.sub(r'[\\/:*?"<>|]+', "", s)
    return s.strip()[:60]


def copy_file(src: pathlib.Path, dst: pathlib.Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  + {dst.relative_to(dst.parents[2])}")


def archive_one(date: str, slug: str, parts: dict, root: pathlib.Path):
    """parts: {topic, draft, html, xhs:[...], wechat:[...], meta:{}}"""
    title = parts.get("title") or slug
    # 去掉标题里可能已带的日期前缀，避免文件夹名出现重复日期
    title = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", title).strip() or slug
    month = date[:7]
    folder = root / month / safe_name(f"{date}-{title}")
    folder.mkdir(parents=True, exist_ok=True)

    if parts.get("topic"):
        copy_file(parts["topic"], folder / "01-选题.md")
    if parts.get("draft"):
        copy_file(parts["draft"], folder / "02-终稿.md")
    if parts.get("html"):
        copy_file(parts["html"], folder / "03-公众号排版.html")
    for i, p in enumerate(parts.get("xhs", []), 1):
        copy_file(p, folder / "04-小红书图" / p.name)
    for p in parts.get("wechat", []):
        copy_file(p, folder / "05-公众号封面" / p.name)

    meta = {"date": date, "slug": slug, "title": title,
            "synced_at": datetime.datetime.now().isoformat(timespec="seconds")}
    meta.update(parts.get("meta", {}))
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ 已归档: {folder}")


def sync_from_archive(root: pathlib.Path) -> set:
    """优先：直接镜像云端 archive/DATE-SLUG/ 目录"""
    done = set()
    if not ARCHIVE.exists():
        return done
    print("\n── 步骤 2a：镜像云端 archive/ ──")
    for d in sorted(ARCHIVE.iterdir()):
        if not d.is_dir():
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(.+)", d.name)
        if not m:
            continue
        date, slug = m.group(1), m.group(2)
        meta = {}
        meta_file = d / "meta.json"
        if meta_file.exists():
            try: meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception: pass
        parts = {
            "title": meta.get("title"),
            "topic":  next(iter(d.glob("*选题*.md")), None) or next(iter(d.glob("topic*.md")), None),
            "draft":  next(iter(d.glob("*终稿*.md")), None) or next(iter(d.glob("draft*.md")), None) or next(iter(d.glob("*.md")), None),
            "html":   next(iter(d.glob("*.html")), None),
            "xhs":    sorted(d.glob("**/xhs-*.png")),
            "wechat": sorted(d.glob("**/wechat-*.png")),
            "meta":   meta,
        }
        archive_one(date, slug, parts, root)
        done.add(date)
    return done


def sync_from_loose(root: pathlib.Path, skip_dates: set):
    """兜底：没有 archive/ 的日期，从 topics/drafts/social-cards 拼装"""
    print("\n── 步骤 2b：拼装散落产物（无 archive 的日期）──")
    for draft in sorted(DRAFTS.glob("20??-??-??-*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(.+)", draft.stem)
        if not m:
            continue
        date, slug = m.group(1), m.group(2)
        if date in skip_dates:
            continue
        topic = TOPICS / f"{date}.md"
        # 匹配 social-cards：目录名包含 slug 关键词
        kw = re.findall(r"[一-鿿A-Za-z0-9]{2,}", slug)[:3]
        xhs, wechat = [], []
        for cd in CARDS.glob("*/"):
            if any(k in cd.name for k in kw):
                xhs += sorted(cd.glob("**/xhs-*.png"))
                wechat += sorted(cd.glob("**/wechat-*.png"))
        # 排版 HTML：format.py 输出到 ~/wechat-out/<draft.stem>/article.html
        html = None
        fmt_sub = WECHAT_OUT / draft.stem / "article.html"
        if fmt_sub.exists():
            html = fmt_sub
        elif (WECHAT_OUT / "article.html").exists():
            html = WECHAT_OUT / "article.html"
        parts = {
            "title": title_of(draft),
            "topic": topic if topic.exists() else None,
            "draft": draft,
            "html": html,
            "xhs": xhs, "wechat": wechat,
            "meta": {"source": "loose"},
        }
        archive_one(date, slug, parts, root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="本地归档根目录")
    ap.add_argument("--no-pull", action="store_true", help="跳过 git pull")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    print(f"本地归档根目录: {root}")

    if not args.no_pull:
        git_pull()

    done = sync_from_archive(root)
    sync_from_loose(root, done)

    print(f"\n[完成] 所有产物已分门别类同步到: {root}")


if __name__ == "__main__":
    main()
