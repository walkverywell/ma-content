"""
push_draft.py — 把文章推送到微信公众号草稿箱
用法：
  python scripts/push_draft.py <markdown文件路径> [封面图路径]
  python scripts/push_draft.py --html <html文件路径> <markdown文件路径> [封面图路径]

--html 模式：直接使用已排版的 HTML 文件内容（如 xiaohu-wechat-format 输出），
            标题从 markdown 文件提取，不再做 md→html 转换。
"""
import sys, os, re, json, pathlib, mimetypes
import urllib.request, urllib.parse

# ── 凭证 ──────────────────────────────────────────────────────────────────────
SECRETS = pathlib.Path.home() / ".ma-secrets" / "wechat.json"
cfg = json.loads(SECRETS.read_text())
APPID   = cfg["appid"]
SECRET  = cfg["appsecret"]

BASE = "https://api.weixin.qq.com"

def api(path, data=None, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode()
        req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# ── access_token ──────────────────────────────────────────────────────────────
def get_token():
    r = api("/cgi-bin/token", params={
        "grant_type": "client_credential",
        "appid": APPID,
        "secret": SECRET,
    })
    if "errcode" in r:
        raise RuntimeError(f"token 失败: {r}")
    return r["access_token"]

# ── 上传封面图（临时素材，永久 thumb） ────────────────────────────────────────
def upload_thumb(token, img_path):
    img_path = pathlib.Path(img_path)
    mime = mimetypes.guess_type(img_path)[0] or "image/png"
    # 使用永久素材接口
    url = f"{BASE}/cgi-bin/material/add_material?access_token={token}&type=image"
    boundary = "----WechatBoundary7MA4YWxkTrZu0gW"
    body  = f"--{boundary}\r\n"
    body += f'Content-Disposition: form-data; name="media"; filename="{img_path.name}"\r\n'
    body += f"Content-Type: {mime}\r\n\r\n"
    body  = body.encode() + img_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"
    })
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read())
    if "errcode" in res and res["errcode"] != 0:
        raise RuntimeError(f"封面上传失败: {res}")
    return res.get("media_id") or res.get("thumb_media_id")

# ── Markdown → HTML ───────────────────────────────────────────────────────────
def md_to_html(text):
    # 标题
    text = re.sub(r'^# (.+)$',   r'<h1>\1</h1>', text, flags=re.M)
    text = re.sub(r'^## (.+)$',  r'<h2>\1</h2>', text, flags=re.M)
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.M)
    # 加粗
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # 分割线
    text = re.sub(r'^---+$', r'<hr/>', text, flags=re.M)
    # 引用块
    lines, out, in_bq = text.split('\n'), [], False
    for line in lines:
        if line.startswith('> '):
            if not in_bq:
                out.append('<blockquote>')
                in_bq = True
            out.append('<p>' + line[2:] + '</p>')
        else:
            if in_bq:
                out.append('</blockquote>')
                in_bq = False
            out.append(line)
    if in_bq:
        out.append('</blockquote>')
    text = '\n'.join(out)
    # 链接
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    # 段落（连续非空行合并）
    paras, buf = [], []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            if buf:
                paras.append(' '.join(buf))
                buf = []
        else:
            buf.append(stripped)
    if buf:
        paras.append(' '.join(buf))
    html_paras = []
    for p in paras:
        if p.startswith('<h') or p.startswith('<hr') or p.startswith('<blockquote'):
            html_paras.append(p)
        else:
            html_paras.append(f'<p>{p}</p>')
    body = '\n'.join(html_paras)
    return f"""<section style="font-family:'PingFang SC','Helvetica Neue',sans-serif;font-size:17px;line-height:1.8;color:#333;max-width:677px;margin:0 auto;">
<style>
h1{{font-size:22px;font-weight:700;margin:24px 0 8px;color:#111;}}
h2{{font-size:19px;font-weight:600;margin:20px 0 8px;color:#111;border-left:4px solid #0431B4;padding-left:10px;}}
h3{{font-size:17px;font-weight:600;margin:16px 0 6px;color:#333;}}
strong{{color:#111;font-weight:700;}}
p{{margin:12px 0;}}
hr{{border:none;border-top:1px solid #e0e0e0;margin:20px 0;}}
blockquote{{background:#f5f5f5;border-left:4px solid #0431B4;margin:16px 0;padding:12px 16px;border-radius:2px;}}
a{{color:#0431B4;text-decoration:none;}}
</style>
{body}
</section>"""

# ── 提取标题 ──────────────────────────────────────────────────────────────────
def extract_title(text):
    m = re.search(r'^# (.+)$', text, re.M)
    return m.group(1).strip() if m else "并购笔记"

# ── 创建草稿 ──────────────────────────────────────────────────────────────────
def create_draft(token, title, html, thumb_id):
    r = api(f"/cgi-bin/draft/add?access_token={token}", data={
        "articles": [{
            "title":          title,
            "author":         "并购笔记",
            "content":        html,
            "thumb_media_id": thumb_id,
            "need_open_comment": 1,
        }]
    })
    if r.get("errcode", 0) != 0:
        raise RuntimeError(f"草稿创建失败: {r}")
    return r["media_id"]

# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    # --html 模式：push_draft.py --html <html_file> <md_file> [img_file]
    if args and args[0] == "--html":
        html_file = pathlib.Path(args[1])
        md_file   = pathlib.Path(args[2])
        img_file  = pathlib.Path(args[3]) if len(args) > 3 else None
        use_html  = True
    else:
        md_file  = pathlib.Path(args[0])
        img_file = pathlib.Path(args[1]) if len(args) > 1 else None
        use_html = False

    # 自动找封面图
    if img_file is None:
        repo = md_file.parent.parent
        candidates = sorted(repo.glob("social-cards/**/wechat/output/wechat-wide*.png")) + \
                     sorted(repo.glob("social-cards/**/xhs/output/xhs-01*.png"))
        img_file = candidates[0] if candidates else None

    # 提取标题和 HTML
    md_text = md_file.read_text(encoding="utf-8")
    title   = extract_title(md_text)
    if use_html:
        html = html_file.read_text(encoding="utf-8")
        print(f"模式: 使用已排版 HTML")
        print(f"HTML: {html_file}")
    else:
        html = md_to_html(md_text)
        print(f"模式: Markdown 转 HTML")

    print(f"文件: {md_file}")
    print(f"封面: {img_file or '无（将失败）'}")
    print(f"标题: {title}")
    print(f"HTML 长度: {len(html)} 字符")

    token = get_token()
    print(f"access_token: {token[:12]}…")

    if img_file is None:
        raise RuntimeError("没有找到封面图，请手动指定")

    thumb_id = upload_thumb(token, img_file)
    print(f"封面 media_id: {thumb_id}")

    draft_id = create_draft(token, title, html, thumb_id)
    print(f"\n[OK] 草稿已推送！media_id: {draft_id}")
    print("   -> 登录公众号后台 -> 草稿箱查看")
