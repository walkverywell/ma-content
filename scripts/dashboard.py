#!/usr/bin/env python3
"""
M&A 内容仪表盘
运行: py scripts/dashboard.py
访问: http://localhost:8899
"""
import json, re, os, sys, pathlib, threading, uuid, subprocess, time, mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import urllib.parse

REPO       = pathlib.Path(__file__).parent.parent
TOPICS_DIR = REPO / "topics"
DRAFTS_DIR = REPO / "drafts"
WECHAT_OUT = pathlib.Path.home() / "wechat-out"
SECRETS    = pathlib.Path.home() / ".ma-secrets"
PORT       = 8899

# ── 后台任务状态 ───────────────────────────────────────────────────────────────
JOBS: dict[str, dict] = {}   # job_id → {status, log, draft_path}

# ── 模型配置 ──────────────────────────────────────────────────────────────────
MODELS = {
    "claude-sonnet": {"label": "Claude Sonnet 4.5",  "provider": "claude-cli"},
    "claude-opus":   {"label": "Claude Opus 4",      "provider": "claude-cli"},
    "deepseek":      {"label": "Deepseek V3 (SiliconFlow)", "provider": "siliconflow"},
}

def has_claude_cli():
    import shutil
    return bool(shutil.which("claude"))

def has_siliconflow_key():
    return bool(os.environ.get("SF_API_KEY") or
                (SECRETS / "siliconflow.json").exists())

# ── 话题解析 ──────────────────────────────────────────────────────────────────

def parse_topics(path: pathlib.Path) -> list[dict]:
    """从选题文件解析话题列表，兼容表格式和列表式两种格式"""
    text = path.read_text(encoding="utf-8")
    date_m = re.match(r"(\d{4}-\d{2}-\d{2})", path.stem)
    date = date_m.group(1) if date_m else path.stem
    topics = []

    # ── 格式1：汇总表 | # | 选题 | 推荐 | 轨道 | 交易日期 | 一句话钩子 |
    table_rows = re.findall(
        r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*([\★☆]+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        text, re.M
    )
    if table_rows:
        for row in table_rows:
            rank, title, stars, track, tx_date, hook = row
            topics.append({
                "rank": int(rank),
                "title": title.strip(),
                "stars": stars.count("★"),
                "track": track.strip(),
                "hook":  hook.strip(),
                "date":  date,
            })
        # 从详细条目补充 hook（如果表格 hook 太短则用条目里的一句话看点）
        for t in topics:
            sec_m = re.search(
                rf"###\s*{t['rank']}[\.、][^\n]*\n(.*?)(?=\n###\s*\d+|$)",
                text, re.DOTALL
            )
            if sec_m:
                hook_m = re.search(r"一句话看点[：:]\*?\*?(.+?)\*?\*?(?:\n|$)", sec_m.group(1))
                if hook_m and len(hook_m.group(1).strip()) > len(t["hook"]):
                    t["hook"] = hook_m.group(1).strip()
        return topics

    # ── 格式2：### N. 标题 段落式
    sections = re.split(r"\n(?=###\s*\d+[\.、])", text)
    for sec in sections:
        m = re.match(r"###\s*(\d+)[\.、]\s*(.+)", sec)
        if not m:
            continue
        rank  = int(m.group(1))
        title = m.group(2).strip()
        hook_m = re.search(r"一句话看点[：:]\*?\*?(.+?)\*?\*?(?:\n|$)", sec)
        stars_m = re.search(r"推荐指数[：:]\s*([\★☆]+)", sec)
        track_m = re.search(r"轨道[：:]?\s*([^\n·|]+)", sec)
        topics.append({
            "rank":  rank,
            "title": title,
            "stars": stars_m.group(1).count("★") if stars_m else 0,
            "track": track_m.group(1).strip() if track_m else "",
            "hook":  hook_m.group(1).strip() if hook_m else "",
            "date":  date,
        })
    return sorted(topics, key=lambda x: x["rank"])


def match_draft(topic: dict, drafts: list[pathlib.Path]) -> pathlib.Path | None:
    """尝试把话题匹配到草稿文件（同日期 + 标题关键词）"""
    date = topic["date"]
    same_day = [d for d in drafts
                if re.match(r"\d{4}-\d{2}-\d{2}", d.stem) and d.stem.startswith(date)]
    if not same_day:
        return None
    title = topic["title"]
    # 提取 2 字以上中文词
    keywords = re.findall(r"[一-鿿]{2,}", title)
    for draft in same_day:
        slug = draft.stem[11:]  # 去掉日期前缀
        if any(kw in slug for kw in keywords):
            return draft
    # 最后兜底：rank==1 匹配当天第一篇草稿
    if topic["rank"] == 1:
        return same_day[0]
    return None


def get_preview_html(draft_path: pathlib.Path) -> str | None:
    """优先返回 wechat-out/article.html；否则 None（会用内置 md→html）"""
    html_path = WECHAT_OUT / "article.html"
    if html_path.exists():
        # 简单用修改时间判断是否对应该草稿（同一天内的最新输出）
        html_mtime = datetime.fromtimestamp(html_path.stat().st_mtime)
        draft_mtime = datetime.fromtimestamp(draft_path.stat().st_mtime)
        if abs((html_mtime - draft_mtime).total_seconds()) < 3600 * 24:
            return str(html_path)
    return None


def all_topics_data() -> dict:
    """返回按 written/unwritten 分组的完整话题数据"""
    all_drafts = list(DRAFTS_DIR.glob("20??-??-??-*.md")) if DRAFTS_DIR.exists() else []
    written, unwritten = [], []

    for topic_file in sorted(TOPICS_DIR.glob("20??-??-??.md"), reverse=True):
        topics = parse_topics(topic_file)
        for t in topics:
            draft = match_draft(t, all_drafts)
            t["draft"] = str(draft) if draft else None
            t["draft_name"] = draft.name if draft else None
            t["preview_html"] = get_preview_html(draft) if draft else None
            if draft:
                written.append(t)
            else:
                unwritten.append(t)

    # 无对应选题文件的草稿（手写稿）
    matched_drafts = {t["draft"] for t in written if t["draft"]}
    for draft in sorted(all_drafts, key=lambda d: d.name, reverse=True):
        if str(draft) not in matched_drafts:
            written.append({
                "rank": 0, "title": draft.stem[11:].replace("-", " "),
                "stars": 0, "track": "", "hook": "",
                "date": draft.stem[:10],
                "draft": str(draft), "draft_name": draft.name,
                "preview_html": get_preview_html(draft),
            })

    cli_ok = has_claude_cli()
    sf_ok  = has_siliconflow_key()
    return {"written": written, "unwritten": unwritten,
            "models": {k: {**v, "available": (
                cli_ok if v["provider"] == "claude-cli" else sf_ok
            )} for k, v in MODELS.items()}}


# ── 写稿任务 ──────────────────────────────────────────────────────────────────

def write_article_job(job_id: str, topic: dict, model_key: str):
    """后台线程：调用写稿脚本"""
    JOBS[job_id] = {"status": "running", "log": [], "draft_path": None}

    def log(msg):
        JOBS[job_id]["log"].append(msg)
        print(f"[{job_id[:6]}] {msg}")

    try:
        script = REPO / "scripts" / "write_article.py"
        if not script.exists():
            log("错误：scripts/write_article.py 不存在，请先确认写稿脚本已安装")
            JOBS[job_id]["status"] = "error"
            return

        topic_json = json.dumps(topic, ensure_ascii=False)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        cmd = [sys.executable, str(script), "--topic", topic_json, "--model", model_key]
        log(f"启动写稿：{model_key} / {topic['title'][:20]}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", env=env)
        draft_path = None
        for line in proc.stdout:
            line = line.rstrip()
            log(line)
            if line.startswith("DRAFT_PATH:"):
                draft_path = line.split(":", 1)[1].strip()

        proc.wait()
        if proc.returncode == 0 and draft_path:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["draft_path"] = draft_path
            log(f"写稿完成：{draft_path}")
        else:
            JOBS[job_id]["status"] = "error"
            log(f"写稿失败（退出码 {proc.returncode}）")
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["log"].append(str(e))


# ── HTTP 处理器 ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str, code=200):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        qs     = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self.send_html(DASHBOARD_HTML)
        elif path == "/api/topics":
            self.send_json(all_topics_data())
        elif path.startswith("/api/status/"):
            job_id = path.split("/")[-1]
            self.send_json(JOBS.get(job_id, {"status": "not_found"}))
        elif path == "/api/preview":
            # /api/preview?file=<encoded_path>
            file_path = qs.get("file", [None])[0]
            if not file_path:
                self.send_json({"error": "missing file param"}, 400); return
            p = pathlib.Path(file_path)
            if not p.exists():
                self.send_json({"error": "not found"}, 404); return
            if p.suffix == ".html":
                self.send_html(p.read_text(encoding="utf-8"))
            elif p.suffix == ".md":
                self.send_html(md_preview(p))
            else:
                self.send_json({"error": "unsupported"}, 400)
        elif path == "/api/open":
            file_path = qs.get("file", [None])[0]
            if file_path and pathlib.Path(file_path).exists():
                subprocess.Popen(["explorer", pathlib.Path(file_path).as_posix().replace("/", "\\")])
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        path   = self.path

        if path == "/api/write":
            topic     = body.get("topic", {})
            model_key = body.get("model", "claude-sonnet")
            if not topic:
                self.send_json({"error": "missing topic"}, 400); return
            job_id = str(uuid.uuid4())
            t = threading.Thread(target=write_article_job, args=(job_id, topic, model_key), daemon=True)
            t.start()
            self.send_json({"job_id": job_id})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ── Markdown → HTML 预览（内置简易版）────────────────────────────────────────

def md_preview(path: pathlib.Path) -> str:
    """把草稿 MD 转成带样式的 HTML 用于预览"""
    text = path.read_text(encoding="utf-8")
    # 引用块
    def repl_bq(m):
        inner = m.group(1).replace("\n> ", "\n")
        return f"<blockquote>{inner}</blockquote>"
    text = re.sub(r"((?:^> .+\n?)+)", repl_bq, text, flags=re.M)
    text = re.sub(r"^> ", "", text, flags=re.M)
    # 标题
    text = re.sub(r"^# (.+)$",   r'<h1>\1</h1>', text, flags=re.M)
    text = re.sub(r"^## (.+)$",  r'<h2>\1</h2>', text, flags=re.M)
    text = re.sub(r"^### (.+)$", r'<h3>\1</h3>', text, flags=re.M)
    # 加粗/分割线
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"^---+$", "<hr/>", text, flags=re.M)
    # 链接
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    # 段落
    paras, buf = [], []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            if buf: paras.append(" ".join(buf)); buf = []
        else:
            buf.append(s)
    if buf: paras.append(" ".join(buf))
    body = "\n".join(
        p if re.match(r"<(h[1-3]|hr|blockquote)", p) else f"<p>{p}</p>"
        for p in paras
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{path.stem}</title>
<style>
body{{font-family:'PingFang SC','Helvetica Neue',sans-serif;font-size:17px;
line-height:1.8;color:#222;max-width:740px;margin:40px auto;padding:0 24px;}}
h1{{font-size:24px;font-weight:700;margin:0 0 24px;color:#0431B4;}}
h2{{font-size:19px;font-weight:600;margin:24px 0 8px;border-left:4px solid #0431B4;padding-left:10px;}}
h3{{font-size:17px;font-weight:600;margin:20px 0 6px;color:#333;}}
strong{{font-weight:700;color:#111;}}
hr{{border:none;border-top:1px solid #e0e0e0;margin:24px 0;}}
blockquote{{background:#f5f7ff;border-left:4px solid #0431B4;margin:16px 0;
padding:12px 16px;border-radius:2px;font-size:15px;color:#555;}}
a{{color:#0431B4;text-decoration:none;}}
p{{margin:10px 0;}}
</style></head><body>{body}</body></html>"""


# ── 仪表盘 HTML ────────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M&A 内容仪表盘</title>
<style>
  :root{--blue:#0431B4;--blue-light:#e8ecff;--green:#00875a;--green-light:#e3fcef;
    --gray:#666;--border:#e5e7eb;--radius:8px;--shadow:0 1px 4px rgba(0,0,0,.08);}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:'PingFang SC','Helvetica Neue',Arial,sans-serif;
    font-size:14px;background:#f8f9fc;color:#222;min-height:100vh;}

  /* ── 顶栏 ── */
  header{background:#fff;border-bottom:2px solid var(--blue);
    padding:0 32px;display:flex;align-items:center;gap:16px;height:56px;
    position:sticky;top:0;z-index:100;box-shadow:var(--shadow);}
  header h1{font-size:17px;font-weight:700;color:var(--blue);letter-spacing:.5px;}
  header .stats{margin-left:auto;display:flex;gap:20px;font-size:13px;color:var(--gray);}
  header .stats b{color:#222;}
  .refresh-btn{border:1px solid var(--border);background:#fff;padding:5px 14px;
    border-radius:var(--radius);cursor:pointer;font-size:13px;color:#444;}
  .refresh-btn:hover{background:#f0f0f0;}

  /* ── 主体 ── */
  main{max-width:1200px;margin:0 auto;padding:28px 24px;}

  /* ── 分区标题 ── */
  .section-header{display:flex;align-items:center;gap:12px;margin:0 0 16px;}
  .section-header h2{font-size:15px;font-weight:700;color:#111;}
  .badge{padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;}
  .badge-written{background:var(--green-light);color:var(--green);}
  .badge-unwritten{background:#fff0e8;color:#d05c00;}
  .section-divider{height:1px;background:var(--border);flex:1;}

  /* ── 卡片网格 ── */
  .card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
    gap:16px;margin-bottom:40px;}

  /* ── 话题卡片 ── */
  .card{background:#fff;border:1px solid var(--border);border-radius:var(--radius);
    padding:18px 20px;display:flex;flex-direction:column;gap:10px;
    transition:box-shadow .15s;position:relative;}
  .card:hover{box-shadow:0 4px 16px rgba(4,49,180,.1);}
  .card.written{border-left:3px solid var(--green);}
  .card.unwritten{border-left:3px solid #f59e0b;}

  .card-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
  .rank-badge{width:24px;height:24px;border-radius:50%;background:var(--blue);
    color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;
    justify-content:center;flex-shrink:0;}
  .rank-badge.no-rank{background:#ccc;}
  .date-tag{font-size:11px;color:var(--gray);background:#f3f4f6;
    padding:2px 8px;border-radius:4px;}
  .track-tag{font-size:11px;color:var(--blue);background:var(--blue-light);
    padding:2px 8px;border-radius:4px;}
  .stars{color:#f59e0b;font-size:12px;letter-spacing:1px;}

  .card-title{font-size:14px;font-weight:600;line-height:1.5;color:#111;}
  .card-hook{font-size:13px;color:#555;line-height:1.6;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}

  /* ── 已写稿操作区 ── */
  .card-actions{display:flex;gap:8px;margin-top:4px;flex-wrap:wrap;}
  .btn{padding:6px 14px;border-radius:6px;font-size:13px;font-weight:500;
    cursor:pointer;border:none;display:inline-flex;align-items:center;gap:5px;
    transition:opacity .15s;}
  .btn:hover{opacity:.85;}
  .btn-primary{background:var(--blue);color:#fff;}
  .btn-outline{background:#fff;color:var(--blue);border:1px solid var(--blue);}
  .btn-green{background:var(--green);color:#fff;}
  .btn-sm{padding:4px 10px;font-size:12px;}

  /* ── 写稿控制区 ── */
  .write-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px;}
  .model-select{border:1px solid var(--border);border-radius:6px;
    padding:5px 10px;font-size:13px;color:#333;background:#fff;cursor:pointer;}
  .model-select:focus{outline:none;border-color:var(--blue);}

  /* ── 预览抽屉 ── */
  #preview-drawer{position:fixed;top:0;right:-50vw;width:50vw;height:100vh;
    background:#fff;box-shadow:-4px 0 24px rgba(0,0,0,.12);
    transition:right .25s ease;z-index:200;display:flex;flex-direction:column;}
  #preview-drawer.open{right:0;}
  #preview-header{padding:14px 20px;border-bottom:1px solid var(--border);
    display:flex;align-items:center;gap:12px;}
  #preview-title{font-weight:600;font-size:14px;flex:1;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  #preview-frame{flex:1;border:none;width:100%;overflow:auto;}
  .close-btn{background:none;border:none;font-size:20px;cursor:pointer;
    color:#666;line-height:1;}

  /* ── 任务进度弹窗 ── */
  #job-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);
    z-index:300;align-items:center;justify-content:center;}
  #job-modal.show{display:flex;}
  #job-box{background:#fff;border-radius:12px;width:520px;max-height:80vh;
    display:flex;flex-direction:column;overflow:hidden;box-shadow:0 16px 48px rgba(0,0,0,.2);}
  #job-box-header{padding:16px 20px;border-bottom:1px solid var(--border);
    display:flex;align-items:center;gap:10px;}
  #job-box-title{font-weight:700;font-size:15px;flex:1;}
  #job-log{flex:1;overflow-y:auto;padding:12px 16px;font-family:monospace;
    font-size:12px;line-height:1.7;color:#333;background:#f8f9fc;min-height:180px;}
  #job-log .log-ok{color:var(--green);}
  #job-log .log-err{color:#e53e3e;}
  #job-box-footer{padding:12px 16px;border-top:1px solid var(--border);
    display:flex;justify-content:flex-end;gap:8px;}

  /* ── 加载态 ── */
  .spinner{width:16px;height:16px;border:2px solid #e5e7eb;
    border-top-color:var(--blue);border-radius:50%;animation:spin .7s linear infinite;
    display:inline-block;}
  @keyframes spin{to{transform:rotate(360deg);}}

  .empty{color:var(--gray);font-size:13px;padding:24px 0;}
  .skeleton{height:140px;background:linear-gradient(90deg,#f0f0f0 25%,#e8e8e8 50%,#f0f0f0 75%);
    background-size:200%;animation:shimmer 1.2s infinite;border-radius:var(--radius);}
  @keyframes shimmer{to{background-position:-200% 0;}}
</style>
</head>
<body>

<header>
  <h1>⚡ M&A 内容仪表盘</h1>
  <div class="stats">
    <span>已写稿 <b id="stat-written">…</b></span>
    <span>待写稿 <b id="stat-unwritten">…</b></span>
    <span>今日 <b id="stat-date">…</b></span>
  </div>
  <button class="refresh-btn" onclick="loadTopics()">↺ 刷新</button>
</header>

<main>
  <div id="content">
    <div class="card-grid" id="skeleton-grid">
      <div class="skeleton"></div><div class="skeleton"></div>
      <div class="skeleton"></div><div class="skeleton"></div>
    </div>
  </div>
</main>

<!-- 预览抽屉 -->
<div id="preview-drawer">
  <div id="preview-header">
    <span id="preview-title">文章预览</span>
    <button class="btn btn-outline btn-sm" onclick="openFile()">在编辑器中打开</button>
    <button class="close-btn" onclick="closePreview()">×</button>
  </div>
  <iframe id="preview-frame" src="about:blank"></iframe>
</div>

<!-- 写稿进度弹窗 -->
<div id="job-modal">
  <div id="job-box">
    <div id="job-box-header">
      <span id="job-box-title">正在写稿…</span>
      <span class="spinner" id="job-spinner"></span>
    </div>
    <div id="job-log"></div>
    <div id="job-box-footer">
      <button class="btn btn-outline" id="job-close-btn" onclick="closeJobModal()" disabled>关闭</button>
      <button class="btn btn-green" id="job-preview-btn" style="display:none"
              onclick="previewJobDraft()">预览稿件</button>
    </div>
  </div>
</div>

<script>
let allData = null;
let currentPreviewFile = null;
let currentJobId = null;
let pollTimer = null;

// ── 加载数据 ────────────────────────────────────────────────────────────────
async function loadTopics() {
  const res = await fetch('/api/topics');
  allData = await res.json();
  renderAll();
}

function renderAll() {
  const d = allData;
  document.getElementById('stat-written').textContent = d.written.length;
  document.getElementById('stat-unwritten').textContent = d.unwritten.length;
  document.getElementById('stat-date').textContent = new Date().toLocaleDateString('zh-CN');

  const content = document.getElementById('content');
  content.innerHTML = '';

  // 未写稿区
  const unSection = buildSection('未写稿话题', d.unwritten, 'unwritten', d.models);
  content.appendChild(unSection);

  // 已写稿区
  const wrSection = buildSection('已写稿', d.written, 'written', d.models);
  content.appendChild(wrSection);
}

function buildSection(title, items, type, models) {
  const wrap = document.createElement('div');
  const badgeClass = type === 'written' ? 'badge-written' : 'badge-unwritten';
  wrap.innerHTML = `
    <div class="section-header">
      <h2>${title}</h2>
      <span class="badge ${badgeClass}">${items.length}</span>
      <div class="section-divider"></div>
    </div>`;
  const grid = document.createElement('div');
  grid.className = 'card-grid';
  if (items.length === 0) {
    grid.innerHTML = '<p class="empty">暂无内容</p>';
  } else {
    items.forEach(t => grid.appendChild(buildCard(t, type, models)));
  }
  wrap.appendChild(grid);
  return wrap;
}

function buildCard(t, type, models) {
  const card = document.createElement('div');
  card.className = `card ${type}`;

  const stars = t.stars ? '★'.repeat(t.stars) + '☆'.repeat(5 - t.stars) : '';
  const rankBadge = t.rank
    ? `<span class="rank-badge">${t.rank}</span>`
    : `<span class="rank-badge no-rank">—</span>`;
  const trackTag = t.track ? `<span class="track-tag">${t.track}</span>` : '';
  const starsHtml = stars ? `<span class="stars">${stars}</span>` : '';

  let actionsHtml = '';
  if (type === 'written') {
    const hasDraft = !!t.draft;
    actionsHtml = `
      <div class="card-actions">
        ${hasDraft ? `<button class="btn btn-outline btn-sm" onclick='openDraft(${JSON.stringify(t.draft)})'>📄 打开稿件</button>` : ''}
        ${hasDraft ? `<button class="btn btn-primary btn-sm" onclick='previewDraft(${JSON.stringify(t.draft)}, ${JSON.stringify(t.preview_html)}, ${JSON.stringify(t.title)})'>👁 预览排版</button>` : ''}
      </div>`;
  } else {
    // 构建模型选项
    const opts = Object.entries(models).map(([k, m]) =>
      `<option value="${k}" ${!m.available ? 'disabled' : ''}>${m.label}${!m.available ? ' (未配置)' : ''}</option>`
    ).join('');
    actionsHtml = `
      <div class="write-controls">
        <select class="model-select" id="model-${t.date}-${t.rank}">${opts}</select>
        <button class="btn btn-primary btn-sm"
                onclick='startWrite(${JSON.stringify(t)}, "model-${t.date}-${t.rank}")'>
          ✏️ 一键写稿
        </button>
      </div>`;
  }

  card.innerHTML = `
    <div class="card-meta">
      ${rankBadge}
      <span class="date-tag">${t.date}</span>
      ${trackTag}
      ${starsHtml}
    </div>
    <div class="card-title">${t.title}</div>
    ${t.hook ? `<div class="card-hook">${t.hook}</div>` : ''}
    ${actionsHtml}`;
  return card;
}

// ── 打开草稿 ────────────────────────────────────────────────────────────────
async function openDraft(filePath) {
  await fetch(`/api/open?file=${encodeURIComponent(filePath)}`);
}

// ── 预览排版 ────────────────────────────────────────────────────────────────
function previewDraft(draftPath, htmlPath, title) {
  const fileToPreview = htmlPath || draftPath;
  currentPreviewFile = draftPath;
  document.getElementById('preview-title').textContent = title || '文章预览';
  document.getElementById('preview-frame').src =
    `/api/preview?file=${encodeURIComponent(fileToPreview)}`;
  document.getElementById('preview-drawer').classList.add('open');
}

function closePreview() {
  document.getElementById('preview-drawer').classList.remove('open');
  document.getElementById('preview-frame').src = 'about:blank';
}

function openFile() {
  if (currentPreviewFile) openDraft(currentPreviewFile);
}

// ── 一键写稿 ────────────────────────────────────────────────────────────────
async function startWrite(topic, selectId) {
  const modelKey = document.getElementById(selectId)?.value || 'claude-sonnet';
  const modal = document.getElementById('job-modal');
  const logEl = document.getElementById('job-log');
  const title = document.getElementById('job-box-title');
  const spinner = document.getElementById('job-spinner');
  const closeBtn = document.getElementById('job-close-btn');
  const previewBtn = document.getElementById('job-preview-btn');

  title.textContent = `写稿中：${topic.title.slice(0, 20)}…`;
  logEl.innerHTML = '';
  spinner.style.display = '';
  closeBtn.disabled = true;
  previewBtn.style.display = 'none';
  modal.classList.add('show');

  const res = await fetch('/api/write', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({topic, model: modelKey})
  });
  const {job_id} = await res.json();
  currentJobId = job_id;
  pollJob(job_id);
}

function pollJob(job_id) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const res = await fetch(`/api/status/${job_id}`);
    const data = await res.json();
    const logEl = document.getElementById('job-log');
    logEl.innerHTML = (data.log || []).map(l => {
      const cls = l.startsWith('错误') || l.includes('失败') ? 'log-err'
                : l.includes('完成') ? 'log-ok' : '';
      return `<div class="${cls}">${escHtml(l)}</div>`;
    }).join('');
    logEl.scrollTop = logEl.scrollHeight;

    if (data.status === 'done' || data.status === 'error') {
      clearInterval(pollTimer);
      document.getElementById('job-spinner').style.display = 'none';
      document.getElementById('job-close-btn').disabled = false;
      document.getElementById('job-box-title').textContent =
        data.status === 'done' ? '写稿完成！' : '写稿失败';
      if (data.status === 'done' && data.draft_path) {
        const previewBtn = document.getElementById('job-preview-btn');
        previewBtn.style.display = '';
        previewBtn._draftPath = data.draft_path;
        loadTopics(); // 刷新列表
      }
    }
  }, 2000);
}

function closeJobModal() {
  document.getElementById('job-modal').classList.remove('show');
  if (pollTimer) clearInterval(pollTimer);
}

function previewJobDraft() {
  const btn = document.getElementById('job-preview-btn');
  if (btn._draftPath) previewDraft(btn._draftPath, null, '新文章预览');
  closeJobModal();
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── 初始化 ──────────────────────────────────────────────────────────────────
loadTopics();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"M&A 内容仪表盘启动中...")
    print(f"仓库路径: {REPO}")
    print(f"选题目录: {TOPICS_DIR}")
    print(f"草稿目录: {DRAFTS_DIR}")
    if not TOPICS_DIR.exists():
        print("  [警告] 选题目录不存在，将显示空列表")
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    import webbrowser
    webbrowser.open(f"http://localhost:{PORT}")
    print(f"\n访问: http://localhost:{PORT}")
    print("按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
