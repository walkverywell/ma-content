#!/usr/bin/env python3
"""
make_cards.py — 根据成稿生成小红书图文(5张) + 公众号封面对(2张)
用法: py scripts/make_cards.py --draft drafts/DATE-SLUG.md

流程：
  1. claude CLI 读稿，按瑞士国际主义风格生成两个 index.html：
       social-cards/DATE-SLUG/xhs/index.html     （5 个 div: xhs-01..05, 各 1080x1440）
       social-cards/DATE-SLUG/wechat/index.html  （wechat-wide 2100x900 + wechat-square 1080x1080）
  2. 用 Playwright 渲染为 PNG（复用 social-cards 下已安装的 playwright，CommonJS + NODE_PATH）

输出 PNG：
  social-cards/DATE-SLUG/xhs/output/xhs-01..05.png
  social-cards/DATE-SLUG/wechat/output/wechat-wide.png, wechat-square.png
"""
import argparse, os, re, sys, subprocess, shutil, pathlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO  = pathlib.Path(__file__).parent.parent
CARDS = REPO / "social-cards"

# 复用任意一个已装好 playwright 的子目录作为 NODE_PATH
def find_node_modules() -> pathlib.Path | None:
    for cand in CARDS.glob("*/node_modules/playwright"):
        return cand.parent
    return None


DESIGN_SPEC = """## 设计规范（瑞士国际主义风格，必须严格遵守）
- 主色：IKB蓝 #0431B4，辅色白 #FFFFFF，文字深墨 #0a0a0b，底色米白 #f8f8f6
- 字体：用 Google Fonts，Inter（数字/英文）+ Noto Sans SC（中文），weight 200-700
- 大量留白、强网格感、超大数字、克制配色

## 文件一：social-cards/{slug_dir}/xhs/index.html（小红书 5 张，每张 1080x1440px）
body 里放 5 个 div，id 依次 xhs-01 / xhs-02 / xhs-03 / xhs-04 / xhs-05，每个 width:1080px;height:1440px;overflow:hidden。
- xhs-01 封面：IKB蓝背景白字，超大核心数字(>=120px)，副标题交易简介，底部 买方 × 标的
- xhs-02 定价拆解：米白底，IKB蓝强调，双栏布局
- xhs-03 支付结构：米白底，账本(ledger)风格，底部一句金句
- xhs-04 关键细节：米白底，隐藏信息点 + 问号悬念
- xhs-05 结语：IKB蓝背景白字，核心金句居中大字，三栏核心数据，开放式问题

## 文件二：social-cards/{slug_dir}/wechat/index.html（公众号封面对）
body 里放 2 个 div：
- id=wechat-wide：width:2100px;height:900px，IKB蓝底白字，左超大核心数字，右金句块（21:9 头图）
- id=wechat-square：width:1080px;height:1080px，米白底，顶部IKB块+核心数字，中部3行核心数据，底部公号名「并购笔记」+日期（1:1 分享卡）

视觉风格两个文件保持一致。"""


def gen_html(draft: pathlib.Path, slug_dir: str):
    out_xhs = CARDS / slug_dir / "xhs"
    out_wc  = CARDS / slug_dir / "wechat"
    out_xhs.mkdir(parents=True, exist_ok=True)
    out_wc.mkdir(parents=True, exist_ok=True)

    prompt = f"""你是资深视觉设计师。请先用 Read 读取文章 `{draft.as_posix()}`，
提取核心数字、买方、标的、对价、3 句关键金句，然后生成两个自包含的 HTML 文件（内联 CSS，引 Google Fonts）。

{DESIGN_SPEC.replace("{slug_dir}", slug_dir)}

## 落盘（用 Write 工具）
- 写 `{out_xhs.as_posix()}/index.html`
- 写 `{out_wc.as_posix()}/index.html`
只输出进度说明，HTML 用 Write 落盘。完成后回复"配图完成"。"""

    cmd = ["claude", "-p", prompt,
           "--allowed-tools", "Read", "Write", "Glob"]
    subprocess.run(cmd, cwd=REPO, encoding="utf-8",
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=600)
    xhs_html = out_xhs / "index.html"
    wc_html  = out_wc / "index.html"
    if not xhs_html.exists() or not wc_html.exists():
        raise RuntimeError("claude 未生成全部 index.html")
    return xhs_html, wc_html


RENDER_CJS = r"""
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const htmlPath = process.argv[2];
const outDir   = process.argv[3];
const specs    = JSON.parse(process.argv[4]); // [{id,file,w,h}]
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);
  for (const s of specs) {
    const el = page.locator('#' + s.id);
    if (await el.count() === 0) { console.log('跳过(无元素): ' + s.id); continue; }
    await el.screenshot({ path: path.join(outDir, s.file), type: 'png' });
    console.log('OK ' + s.file);
  }
  await browser.close();
})();
"""


def render(html: pathlib.Path, out_dir: pathlib.Path, specs: list, node_modules: pathlib.Path):
    """把 render.cjs 放进有 node_modules 的目录里，用本地解析（最稳，避免 NODE_PATH 解析问题）"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir = node_modules.parent  # 含 node_modules 的目录
    render_js = pkg_dir / "_render_cards.cjs"
    render_js.write_text(RENDER_CJS, encoding="utf-8")
    import json as _json
    try:
        r = subprocess.run(
            ["node", str(render_js), str(html), str(out_dir), _json.dumps(specs)],
            cwd=pkg_dir, capture_output=True, text=True, encoding="utf-8", timeout=300
        )
        print(r.stdout.strip())
        if r.returncode != 0:
            print(f"[渲染错误] {r.stderr[:300]}")
    finally:
        render_js.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    args = ap.parse_args()

    draft = pathlib.Path(args.draft)
    if not draft.is_absolute():
        draft = REPO / draft
    if not draft.exists():
        print(f"稿件不存在: {draft}"); sys.exit(1)
    slug_dir = draft.stem  # DATE-SLUG

    nm = find_node_modules()
    if not nm:
        print("[警告] 未找到已安装的 playwright，跳过渲染（仅生成 HTML）")

    print(f"生成配图 HTML：{slug_dir}")
    xhs_html, wc_html = gen_html(draft, slug_dir)

    if nm:
        print("渲染小红书 5 图…")
        render(xhs_html, xhs_html.parent / "output",
               [{"id": f"xhs-0{i}", "file": f"xhs-0{i}.png"} for i in range(1, 6)], nm)
        print("渲染公众号封面对…")
        render(wc_html, wc_html.parent / "output",
               [{"id": "wechat-wide", "file": "wechat-wide.png"},
                {"id": "wechat-square", "file": "wechat-square.png"}], nm)

    print(f"[完成] 配图输出在 social-cards/{slug_dir}/")


if __name__ == "__main__":
    main()
