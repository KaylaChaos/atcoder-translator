# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from email.utils import formatdate
from html.parser import HTMLParser
from urllib.parse import quote, urlparse
import urllib.error
import urllib.request


VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

DEFAULT_WATERMARK_WIDTH = "180mm"
DEFAULT_WATERMARK_OPACITY = "0.075"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ms_since(start):
    return int((time.perf_counter() - start) * 1000)


def env(name, default=""):
    return os.getenv(name, default).strip()


def normalize_base_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        return "https://api.openai.com/v1"
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")


def http_request(method, url, headers=None, body=None, timeout=30):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.perf_counter()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return {
                "ok": True,
                "status": resp.status,
                "ms": ms_since(start),
                "bytes": len(raw),
                "body": raw.decode("utf-8", errors="replace"),
                "body_bytes": raw,
                "body_preview": raw[:800].decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as e:
        raw = e.read()
        return {
            "ok": False,
            "status": e.code,
            "ms": ms_since(start),
            "bytes": len(raw),
            "error": raw[:1200].decode("utf-8", errors="replace"),
            "body_bytes": raw,
        }
    except Exception as e:
        return {
            "ok": False,
            "ms": ms_since(start),
            "error": repr(e),
        }


def get_atcoder_cookie():
    cookie_b64 = env("ATCODER_COOKIE_B64")
    revel_session = env("ATCODER_REVEL_SESSION")

    if cookie_b64:
        cookie = base64.b64decode(cookie_b64).decode("utf-8").strip()
        source = "ATCODER_COOKIE_B64"
    elif revel_session:
        cookie = "REVEL_SESSION=" + revel_session
        source = "ATCODER_REVEL_SESSION"
    else:
        cookie = env("ATCODER_COOKIE")
        source = "ATCODER_COOKIE" if cookie else "none"

    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()

    cookie = " ".join(cookie.replace("\r", " ").replace("\n", " ").split())
    return cookie, source


def atcoder_headers():
    cookie, _ = get_atcoder_cookie()
    headers = {
        "User-Agent": env(
            "ATCODER_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36",
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,zh-CN;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if cookie and cookie.upper() not in ("NONE", "NULL", "DISABLED", "-"):
        headers["Cookie"] = cookie
    return headers


def atcoder_get(url, timeout=25):
    return http_request("GET", url, headers=atcoder_headers(), timeout=timeout)


def oss_config():
    return {
        "endpoint": env("ALI_OSS_ENDPOINT"),
        "bucket": env("ALI_OSS_BUCKET"),
        "access_key_id": env("ALI_ACCESS_KEY_ID"),
        "access_key_secret": env("ALI_ACCESS_KEY_SECRET"),
        "security_token": env("ALI_SECURITY_TOKEN"),
        "prefix": env("ALI_OSS_PREFIX", "atcoder-translator").strip("/"),
    }


def require_oss_config(cfg):
    missing = [
        k for k in ("endpoint", "bucket", "access_key_id", "access_key_secret")
        if not cfg.get(k)
    ]
    if missing:
        raise RuntimeError("missing OSS env vars: " + ", ".join(missing))


def oss_request(method, endpoint, bucket, key, access_key_id, access_key_secret,
                security_token="", body=None, content_type=""):
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint

    parsed = urlparse(endpoint)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path
    object_path = "/" + quote(key, safe="/")
    url = f"{scheme}://{bucket}.{host}{object_path}"

    date = formatdate(timeval=None, localtime=False, usegmt=True)
    body = body or b""
    content_md5 = ""
    if method == "PUT":
        content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")

    oss_headers = {}
    if security_token:
        oss_headers["x-oss-security-token"] = security_token

    canonical_oss_headers = ""
    for h in sorted(oss_headers):
        canonical_oss_headers += f"{h}:{oss_headers[h]}\n"

    canonical_resource = f"/{bucket}/{key}"
    string_to_sign = "\n".join([
        method,
        content_md5,
        content_type,
        date,
        canonical_oss_headers + canonical_resource,
    ])
    signature = base64.b64encode(
        hmac.new(
            access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")

    headers = {
        "Date": date,
        "Authorization": f"OSS {access_key_id}:{signature}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if content_md5:
        headers["Content-MD5"] = content_md5
    headers.update(oss_headers)

    try:
        req = urllib.request.Request(
            url,
            data=body if method == "PUT" else None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return {
                "ok": 200 <= resp.status < 300,
                "status": resp.status,
                "body_bytes": raw,
                "body": raw.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as e:
        raw = e.read()
        return {
            "ok": False,
            "status": e.code,
            "error": raw[:1500].decode("utf-8", errors="replace"),
            "body_bytes": raw,
        }
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def oss_get_text(cfg, key, default=None):
    res = oss_request(
        "GET", cfg["endpoint"], cfg["bucket"], key,
        cfg["access_key_id"], cfg["access_key_secret"], cfg.get("security_token", ""),
    )
    if res.get("ok"):
        return res.get("body", "")
    if res.get("status") == 404:
        return default
    raise RuntimeError(f"OSS GET failed for {key}: {res}")


def oss_get_bytes(cfg, key, default=None):
    res = oss_request(
        "GET", cfg["endpoint"], cfg["bucket"], key,
        cfg["access_key_id"], cfg["access_key_secret"], cfg.get("security_token", ""),
    )
    if res.get("ok"):
        return res.get("body_bytes", b"")
    if res.get("status") == 404:
        return default
    raise RuntimeError(f"OSS GET failed for {key}: {res}")


def oss_put_bytes(cfg, key, data, content_type="application/octet-stream"):
    res = oss_request(
        "PUT", cfg["endpoint"], cfg["bucket"], key,
        cfg["access_key_id"], cfg["access_key_secret"], cfg.get("security_token", ""),
        body=data,
        content_type=content_type,
    )
    if not res.get("ok"):
        raise RuntimeError(f"OSS PUT failed for {key}: {res}")
    return res


def oss_put_json(cfg, key, value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    return oss_put_bytes(cfg, key, data, "application/json; charset=utf-8")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_title(page_html):
    m = re.search(r"<title>(.*?)</title>", page_html, re.I | re.S)
    if not m:
        return ""
    return html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())


def parse_tasks(contest_id, tasks_html):
    pattern = re.compile(
        rf'href="(/contests/{re.escape(contest_id)}/tasks/([^"#?]+))"',
        re.I,
    )
    tasks = []
    seen = set()
    for m in pattern.finditer(tasks_html):
        task_id = html.unescape(m.group(2))
        if task_id in seen:
            continue
        seen.add(task_id)
        tasks.append({
            "task_id": task_id,
            "url": "https://atcoder.jp" + html.unescape(m.group(1)) + "?lang=en",
        })
    return tasks


def contest_ids_in_html(fragment):
    ids = []
    seen = set()
    for cid in re.findall(r'/contests/(abc\d+)', fragment, flags=re.I):
        cid = cid.lower()
        if cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


def html_section_after_heading(page_html, heading):
    idx = page_html.find(heading)
    if idx < 0:
        return ""
    next_idx = len(page_html)
    for other in (
        "Ongoing Contests",
        "Active Contests",
        "Permanent Contests",
        "Upcoming Contests",
        "Recent Contests",
    ):
        if other == heading:
            continue
        j = page_html.find(other, idx + len(heading))
        if j >= 0:
            next_idx = min(next_idx, j)
    return page_html[idx:next_idx]


def resolve_auto_contest_id():
    candidates = resolve_auto_contest_ids()
    if candidates:
        return candidates[0]
    raise RuntimeError("cannot find any ABC contest id on AtCoder contests page")


def resolve_auto_contest_ids():
    url = env("ATCODER_CONTESTS_URL", "https://atcoder.jp/contests/?lang=en")
    res = atcoder_get(url)
    if not res.get("ok"):
        raise RuntimeError(f"failed to fetch contests page for auto contest id: {res}")

    page_html = res.get("body", "")
    mode = env("ATCODER_AUTO_CONTEST_MODE", "active_or_next").lower()

    active = contest_ids_in_html(html_section_after_heading(page_html, "Ongoing Contests"))
    for cid in contest_ids_in_html(html_section_after_heading(page_html, "Active Contests")):
        if cid not in active:
            active.append(cid)
    upcoming = contest_ids_in_html(html_section_after_heading(page_html, "Upcoming Contests"))
    recent = contest_ids_in_html(html_section_after_heading(page_html, "Recent Contests"))
    all_ids = contest_ids_in_html(page_html)

    if mode == "latest_number" and all_ids:
        return [max(all_ids, key=lambda cid: int(cid[3:]))]

    candidates = []

    # Default policy:
    # 1. Prefer an ABC that is currently active.
    # 2. Try the nearest upcoming ABC candidates. Their /tasks pages are often
    #    404 before the contest starts, so run_worker will skip inaccessible
    #    candidates and keep trying.
    # 3. Otherwise prefer the newest already-public ABC from Recent Contests.
    upcoming_limit = int(env("ATCODER_AUTO_UPCOMING_LIMIT", "3"))
    for group in (active, upcoming[:upcoming_limit], recent):
        for cid in group:
            if cid not in candidates:
                candidates.append(cid)

    if not candidates and all_ids:
        for cid in sorted(all_ids, key=lambda x: int(x[3:]), reverse=True):
            if cid not in candidates:
                candidates.append(cid)

    return candidates


class SpanInnerExtractor(HTMLParser):
    def __init__(self, class_name):
        super().__init__(convert_charrefs=False)
        self.class_name = class_name
        self.capturing = False
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        attr_map = dict(attrs)
        classes = attr_map.get("class", "").split()
        if not self.capturing and tag.lower() == "span" and self.class_name in classes:
            self.capturing = True
            self.stack = ["span"]
            return
        if self.capturing:
            self.parts.append(raw)
            if tag.lower() not in VOID_TAGS:
                self.stack.append(tag.lower())

    def handle_startendtag(self, tag, attrs):
        if self.capturing:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if not self.capturing:
            return
        tag = tag.lower()
        if len(self.stack) == 1 and self.stack[-1] == tag:
            self.capturing = False
            self.stack = []
            return
        self.parts.append(f"</{tag}>")
        if self.stack:
            if self.stack[-1] == tag:
                self.stack.pop()
            elif tag in self.stack:
                while self.stack and self.stack[-1] != tag:
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()

    def handle_data(self, data):
        if self.capturing:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self.capturing:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.capturing:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        if self.capturing:
            self.parts.append(f"<!--{data}-->")


class PlainTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_stack = []
        self.pre_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return
        if tag in ("p", "div", "section", "h1", "h2", "h3", "li", "ul", "ol", "pre"):
            self.parts.append("\n")
        if tag == "br":
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "pre":
            self.pre_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip_stack:
            if self.skip_stack[-1] == tag:
                self.skip_stack.pop()
            return
        if tag in ("p", "div", "section", "h1", "h2", "h3", "li", "ul", "ol", "pre"):
            self.parts.append("\n")
        if tag == "pre" and self.pre_depth:
            self.pre_depth -= 1

    def handle_data(self, data):
        if self.skip_stack:
            return
        self.parts.append(data)


def html_to_plain_text(fragment):
    parser = PlainTextExtractor()
    parser.feed(fragment)
    text = "".join(parser.parts)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_english_statement(page_html):
    extractor = SpanInnerExtractor("lang-en")
    extractor.feed(page_html)
    html_part = "".join(extractor.parts).strip()
    if html_part:
        return html_part

    m = re.search(r'<div[^>]+id="task-statement"[^>]*>(.*?)</div>\s*</div>', page_html, re.I | re.S)
    if m:
        return m.group(1).strip()
    return ""


HEADING_MAP = {
    "Problem Statement": "题目描述",
    "Constraints": "约束",
    "Input": "输入",
    "Output": "输出",
    "Notes": "注意",
}


def is_sample_heading(text):
    return bool(re.match(r"Sample (Input|Output) \d+", text.strip(), re.I))


def translate_heading(text):
    s = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if s in HEADING_MAP:
        return HEADING_MAP[s]
    m = re.match(r"Sample Input (\d+)", s, re.I)
    if m:
        return f"样例输入 {m.group(1)}"
    m = re.match(r"Sample Output (\d+)", s, re.I)
    if m:
        return f"样例输出 {m.group(1)}"
    return None


def protect_html_tokens(fragment):
    placeholders = {}

    def put(value):
        key = f"__HTML_{len(placeholders):04d}__"
        placeholders[key] = value
        return key

    protected = re.sub(
        r"<(var|code|pre|kbd|samp|script|style)\b[^>]*>.*?</\1>",
        lambda m: put(m.group(0)),
        fragment,
        flags=re.I | re.S,
    )
    protected = re.sub(r"<[^>]+>", lambda m: put(m.group(0)), protected)
    text = html.unescape(protected)
    return text, placeholders


def restore_html_tokens(text, placeholders):
    out = text
    for key, value in placeholders.items():
        out = out.replace(key, value)
    return out


def collect_translation_items(statement_html):
    items = []
    replacements = {}

    block_re = re.compile(r"<(h3|p|li)([^>]*)>(.*?)</\1>", re.I | re.S)

    for idx, m in enumerate(block_re.finditer(statement_html)):
        tag = m.group(1).lower()
        inner = m.group(3)
        plain = re.sub(r"<[^>]+>", "", inner)
        plain = html.unescape(re.sub(r"\s+", " ", plain)).strip()
        item_id = f"t{idx:04d}"

        if tag == "h3":
            mapped = translate_heading(plain)
            if mapped:
                replacements[item_id] = (m.span(3), mapped)
                continue

        if not plain:
            continue
        if tag in ("p", "li") and len(plain) <= 2:
            continue

        source, placeholders = protect_html_tokens(inner)
        source = re.sub(r"\s+", " ", source).strip()
        if not source:
            continue

        items.append({
            "id": item_id,
            "text": source,
            "placeholders": placeholders,
            "span": m.span(3),
        })

    return items, replacements


def parse_json_array(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("model output is not a JSON array")


def parse_json_object(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start:end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("model output is not a JSON object")


def extract_responses_text(resp_json):
    if "output_text" in resp_json:
        return resp_json["output_text"]

    texts = []
    for item in resp_json.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text"):
                texts.append(content.get("text", ""))
    return "".join(texts)


def openai_generate_text(prompt, max_tokens, model_override=""):
    api_key = env("OPENAI_API_KEY")
    model = model_override or env("OPENAI_MODEL")
    base_url = normalize_base_url(env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    mode = env("OPENAI_API_MODE", "auto").lower()

    if not api_key:
        raise RuntimeError("missing OPENAI_API_KEY")
    if not model:
        raise RuntimeError("missing OPENAI_MODEL")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def call_responses():
        body = {
            "model": model,
            "input": prompt,
            "store": False,
            "max_output_tokens": max_tokens,
        }
        res = http_request("POST", f"{base_url}/responses", headers=headers, body=body, timeout=120)
        if not res.get("ok"):
            raise RuntimeError(f"OpenAI responses failed: {res}")
        return extract_responses_text(json.loads(res["body"]))

    def call_chat():
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        res = http_request("POST", f"{base_url}/chat/completions", headers=headers, body=body, timeout=120)
        if not res.get("ok"):
            raise RuntimeError(f"OpenAI chat completions failed: {res}")
        data = json.loads(res["body"])
        return data["choices"][0]["message"]["content"]

    if mode == "responses":
        return call_responses()
    if mode == "chat":
        return call_chat()

    try:
        return call_responses()
    except Exception:
        return call_chat()


def translate_items(items):
    if not items:
        return {}

    batch_size = int(env("TRANSLATE_BATCH_SIZE", "18"))
    max_tokens = int(env("OPENAI_TRANSLATE_MAX_TOKENS", "4096"))
    translated = {}

    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        payload = [{"id": item["id"], "text": item["text"]} for item in batch]
        prompt = (
            "你是算法竞赛题面翻译器。把 JSON 数组中每个 text 翻译成简体中文。\n"
            "要求：\n"
            "1. 保留所有 __HTML_0000__ 这类占位符，必须逐字原样保留。\n"
            "2. 保留变量名、数学符号、代码字面量、输入输出内容，不要解释题目，不要添加解法。\n"
            "3. 只输出 JSON 数组，元素格式为 {\"id\":\"...\",\"zh\":\"...\"}。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        raw = openai_generate_text(prompt, max_tokens)
        data = parse_json_array(raw)
        for obj in data:
            if isinstance(obj, dict) and "id" in obj and "zh" in obj:
                translated[str(obj["id"])] = str(obj["zh"])

    return translated


def translate_statement(statement_html):
    items, direct_replacements = collect_translation_items(statement_html)
    translated = translate_items(items)

    spans = []
    for item in items:
        zh = translated.get(item["id"])
        if not zh:
            continue
        restored = restore_html_tokens(zh, item["placeholders"])
        spans.append((item["span"], restored))

    for item_id, (span, value) in direct_replacements.items():
        spans.append((span, html.escape(value)))

    spans.sort(key=lambda x: x[0][0], reverse=True)
    out = statement_html
    for (lo, hi), value in spans:
        out = out[:lo] + value + out[hi:]
    return out, {
        "translated_blocks": len(spans),
        "model_blocks": len(items),
        "direct_blocks": len(direct_replacements),
    }


def render_translated_html(contest_id, task_id, title, task_url, statement_html, meta):
    escaped_title = html.escape(title or task_id)
    escaped_url = html.escape(task_url)
    generated_at = html.escape(now_iso())
    meta_json = html.escape(json.dumps(meta, ensure_ascii=False))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title} - 中文翻译</title>
  <link rel="stylesheet" href="https://img.atcoder.jp/public/0a24dba/css/cdn/katex.min.css">
  <script defer src="https://img.atcoder.jp/public/0a24dba/js/cdn/katex.min.js"></script>
  <script defer src="https://img.atcoder.jp/public/0a24dba/js/cdn/auto-render.min.js"></script>
  <script>
    window.__ATCODER_TRANSLATOR_KATEX_DONE = false;
    function renderAtCoderMath() {{
      if (!window.renderMathInElement) {{
        setTimeout(renderAtCoderMath, 50);
        return;
      }}
      document.querySelectorAll('var').forEach(function(el) {{
        if (el.dataset.katexPrepared === '1') return;
        var source = el.innerHTML.replace(/<sub>/g, '_{{').replace(/<\\/sub>/g, '}}');
        el.innerHTML = '\\\\(' + source + '\\\\)';
        el.dataset.katexPrepared = '1';
      }});
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: "$$", right: "$$", display: true}},
          {{left: "\\\\(", right: "\\\\)", display: false}},
          {{left: "\\\\[", right: "\\\\]", display: true}}
        ],
        ignoredTags: ["script", "noscript", "style", "textarea", "code", "option"],
        ignoredClasses: ["prettyprint", "source-code-for-copy"],
        throwOnError: false
      }});
      window.__ATCODER_TRANSLATOR_KATEX_DONE = true;
    }}
    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', renderAtCoderMath);
    }} else {{
      renderAtCoderMath();
    }}
  </script>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC",
        "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.65;
      color: #17202a;
      margin: 28px auto;
      max-width: 920px;
      padding: 0 22px 40px;
    }}
    header {{
      border-bottom: 1px solid #d8dee4;
      margin-bottom: 24px;
      padding-bottom: 14px;
    }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h3 {{ margin-top: 26px; font-size: 18px; border-left: 4px solid #2f6feb; padding-left: 10px; }}
    a {{ color: #0969da; }}
    pre {{
      background: #f6f8fa;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      overflow-x: auto;
      padding: 12px;
      line-height: 1.45;
    }}
    code {{
      background: #f6f8fa;
      border-radius: 4px;
      padding: 0.1em 0.3em;
    }}
    var {{ font-family: "Times New Roman", serif; font-style: italic; }}
    .katex {{ font-size: 1.04em; }}
    .katex-display {{ overflow-x: auto; overflow-y: hidden; }}
    .notice {{
      background: #fff8c5;
      border: 1px solid #eac54f;
      border-radius: 6px;
      padding: 10px 12px;
      margin: 14px 0 20px;
    }}
    .meta {{ color: #57606a; font-size: 13px; }}
    hr {{ border: 0; border-top: 1px solid #d8dee4; margin: 24px 0; }}
  </style>
</head>
<body>
  <header>
    <h1>{escaped_title} - 中文翻译</h1>
    <div class="meta">Contest: {html.escape(contest_id)} / Task: {html.escape(task_id)}</div>
    <div class="meta">Original: <a href="{escaped_url}">{escaped_url}</a></div>
    <div class="meta">Generated: {generated_at}</div>
  </header>
  <div class="notice">自动翻译，仅供内部学习使用。变量、公式、代码块和样例尽量保持原样；如有歧义，请以原题为准。</div>
  <main id="task-statement">
{statement_html}
  </main>
  <footer class="meta" data-meta="{meta_json}"></footer>
</body>
</html>
"""


def katex_head_html():
    return """
  <link rel="stylesheet" href="https://img.atcoder.jp/public/0a24dba/css/cdn/katex.min.css">
  <script defer src="https://img.atcoder.jp/public/0a24dba/js/cdn/katex.min.js"></script>
  <script defer src="https://img.atcoder.jp/public/0a24dba/js/cdn/auto-render.min.js"></script>
  <script>
    window.__ATCODER_TRANSLATOR_KATEX_DONE = false;
    function renderAtCoderMath() {
      if (!window.renderMathInElement) {
        setTimeout(renderAtCoderMath, 50);
        return;
      }
      document.querySelectorAll('var').forEach(function(el) {
        if (el.dataset.katexPrepared === '1') return;
        var source = el.innerHTML.replace(/<sub>/g, '_{').replace(/<\\/sub>/g, '}');
        el.innerHTML = '\\\\(' + source + '\\\\)';
        el.dataset.katexPrepared = '1';
      });
      renderMathInElement(document.body, {
        delimiters: [
          {left: "$$", right: "$$", display: true},
          {left: "\\\\(", right: "\\\\)", display: false},
          {left: "\\\\[", right: "\\\\]", display: true}
        ],
        ignoredTags: ["script", "noscript", "style", "textarea", "code", "option"],
        ignoredClasses: ["prettyprint", "source-code-for-copy"],
        throwOnError: false
      });
      window.__ATCODER_TRANSLATOR_KATEX_DONE = true;
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', renderAtCoderMath);
    } else {
      renderAtCoderMath();
    }
  </script>
"""


def file_data_uri(path):
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    mime = "image/png"
    if path.lower().endswith(".jpg") or path.lower().endswith(".jpeg"):
        mime = "image/jpeg"
    return f"data:{mime};base64,{data}"


def build_pdf_watermark_html():
    watermark_path = env("WATERMARK_PATH", "watermark.png")
    if not os.path.isabs(watermark_path):
        watermark_path = os.path.join(os.path.dirname(__file__), watermark_path)
    if not os.path.exists(watermark_path):
        return ""

    width = env("WATERMARK_WIDTH", DEFAULT_WATERMARK_WIDTH)
    opacity = env("WATERMARK_OPACITY", DEFAULT_WATERMARK_OPACITY)
    data_uri = file_data_uri(watermark_path)

    return f"""
<style id="pdf-watermark-style">
  @media print {{
    * {{
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }}
    .pdf-watermark {{
      position: fixed;
      left: 50%;
      top: 50%;
      width: {width};
      height: auto;
      transform: translate(-50%, -50%);
      opacity: {opacity};
      z-index: 9999;
      pointer-events: none;
      user-select: none;
    }}
  }}
</style>
<img class="pdf-watermark" src="{data_uri}" alt="">
"""


def inject_before_body_end(html_text, addition):
    if not addition:
        return html_text
    idx = html_text.lower().rfind("</body>")
    if idx >= 0:
        return html_text[:idx] + addition + html_text[idx:]
    return html_text + addition


def render_pdf_bytes(html_text):
    from playwright.sync_api import sync_playwright

    watermarked = inject_before_body_end(html_text, build_pdf_watermark_html())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1600})
        page.set_content(watermarked, wait_until="networkidle")
        try:
            page.wait_for_function("window.__ATCODER_TRANSLATOR_KATEX_DONE === true", timeout=8000)
        except Exception:
            pass
        page.emulate_media(media="print")
        pdf = page.pdf(
            format=env("PDF_PAGE_FORMAT", "A4"),
            print_background=True,
            prefer_css_page_size=False,
            margin={
                "top": env("PDF_MARGIN_TOP", "14mm"),
                "right": env("PDF_MARGIN_RIGHT", "13mm"),
                "bottom": env("PDF_MARGIN_BOTTOM", "15mm"),
                "left": env("PDF_MARGIN_LEFT", "13mm"),
            },
        )
        browser.close()
        return pdf


def wecom_webhook_key():
    key = env("WECOM_WEBHOOK_KEY")
    url = env("WECOM_WEBHOOK_URL")
    if key:
        return key
    if "key=" in url:
        return url.split("key=", 1)[1].split("&", 1)[0]
    return ""


def wecom_send_text(content):
    key = wecom_webhook_key()
    if not key:
        raise RuntimeError("missing WECOM_WEBHOOK_KEY")
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    res = http_request("POST", url, body={"msgtype": "text", "text": {"content": content}}, timeout=20)
    if not res.get("ok"):
        raise RuntimeError(f"WeCom text send HTTP failed: {res}")
    data = json.loads(res["body"])
    if data.get("errcode") != 0:
        raise RuntimeError(f"WeCom text send failed: {data}")
    return data


def http_multipart_file(url, field_name, filename, content, content_type, timeout=60):
    boundary = "----fg-boundary-" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + content + tail
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return {
                "ok": True,
                "status": resp.status,
                "ms": ms_since(start),
                "body": raw.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as e:
        raw = e.read()
        return {
            "ok": False,
            "status": e.code,
            "ms": ms_since(start),
            "error": raw[:1200].decode("utf-8", errors="replace"),
        }
    except Exception as e:
        return {"ok": False, "ms": ms_since(start), "error": repr(e)}


def wecom_send_file(filename, content, content_type="text/html"):
    key = wecom_webhook_key()
    if not key:
        raise RuntimeError("missing WECOM_WEBHOOK_KEY")

    upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={key}&type=file"
    upload = http_multipart_file(upload_url, "media", filename, content, content_type)
    if not upload.get("ok"):
        raise RuntimeError(f"WeCom upload HTTP failed: {upload}")
    upload_data = json.loads(upload["body"])
    if upload_data.get("errcode") != 0:
        raise RuntimeError(f"WeCom upload failed: {upload_data}")

    media_id = upload_data.get("media_id")
    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    send = http_request(
        "POST",
        send_url,
        body={"msgtype": "file", "file": {"media_id": media_id}},
        timeout=20,
    )
    if not send.get("ok"):
        raise RuntimeError(f"WeCom file send HTTP failed: {send}")
    send_data = json.loads(send["body"])
    if send_data.get("errcode") != 0:
        raise RuntimeError(f"WeCom file send failed: {send_data}")

    return {"upload": upload_data, "send": send_data}


def load_status(cfg, contest_id):
    key = f"{cfg['prefix']}/status/{contest_id}.json"
    raw = oss_get_text(cfg, key, default="")
    if not raw:
        return {"contest_id": contest_id, "tasks": {}, "created_at": now_iso()}
    try:
        return json.loads(raw)
    except Exception:
        return {"contest_id": contest_id, "tasks": {}, "created_at": now_iso(), "status_parse_error": True}


def save_status(cfg, contest_id, status):
    status["contest_id"] = contest_id
    status["updated_at"] = now_iso()
    key = f"{cfg['prefix']}/status/{contest_id}.json"
    oss_put_json(cfg, key, status)


def get_contest_id(event):
    if isinstance(event, dict):
        value = event.get("contest_id") or event.get("contest")
        if value:
            value = str(value).strip()
            if value.lower() not in ("auto", "latest", "latest_abc", "next_abc"):
                return value

    value = env("ATCODER_CONTEST_ID", "auto")
    if value.lower() in ("", "auto", "latest", "latest_abc", "next_abc"):
        return resolve_auto_contest_id()
    return value


def parse_event_json(value):
    current = value
    for _ in range(3):
        if isinstance(current, dict):
            return current
        if not isinstance(current, str) or not current.strip():
            return None
        try:
            current = json.loads(current)
        except Exception:
            return None
    return current if isinstance(current, dict) else None


def normalize_event(event):
    parsed_event = parse_event_json(event)
    if parsed_event is not None:
        event = parsed_event
    if not isinstance(event, dict):
        return {"_raw_event_type": type(event).__name__}

    normalized = dict(event)

    for key in ("body", "Body"):
        parsed = parse_event_json(event.get(key))
        if isinstance(parsed, dict):
            normalized.update(parsed)

    for key in ("user_event", "UserEvent", "userEvent"):
        parsed = parse_event_json(normalized.get(key))
        if isinstance(parsed, dict):
            if isinstance(parsed.get("input"), dict):
                parsed = parsed["input"]
            normalized.update(parsed)

    return normalized


def is_auto_contest_request(event):
    if isinstance(event, dict):
        value = event.get("contest_id") or event.get("contest")
        if value:
            return str(value).strip().lower() in ("auto", "latest", "latest_abc", "next_abc")
    value = env("ATCODER_CONTEST_ID", "auto")
    return value.lower() in ("", "auto", "latest", "latest_abc", "next_abc")


def get_target_task_ids(event):
    if isinstance(event, dict) and event.get("task_ids"):
        value = event["task_ids"]
        if isinstance(value, list):
            return {str(x).strip() for x in value if str(x).strip()}
        return {x.strip() for x in str(value).split(",") if x.strip()}
    raw = env("ATCODER_TASK_IDS")
    return {x.strip() for x in raw.split(",") if x.strip()}


def run_worker(event=None):
    started = time.perf_counter()
    cfg = oss_config()
    require_oss_config(cfg)

    auto_contest = is_auto_contest_request(event)
    contest_id = get_contest_id(event)
    target_task_ids = get_target_task_ids(event)
    max_tasks = int(env("MAX_TASKS_PER_RUN", "8"))
    force = env("FORCE_REPROCESS", "0") == "1"
    send_enabled = env("WECOM_SEND", "0") == "1"
    output_format = env("OUTPUT_FORMAT", "html").lower()

    report = {
        "ok": True,
        "mode": "worker",
        "image_build_id": env("IMAGE_BUILD_ID", "unknown"),
        "contest_id": contest_id,
        "send_enabled": send_enabled,
        "output_format": output_format,
        "time": now_iso(),
        "tasks": [],
    }
    if isinstance(event, dict):
        report["event_debug"] = {
            "keys": sorted(str(k) for k in event.keys() if not str(k).startswith("_raw")),
            "contest_id": event.get("contest_id") or event.get("contest"),
            "task_ids": event.get("task_ids"),
            "trigger_type": event.get("trigger_type"),
            "trigger_name": event.get("trigger_name"),
            "has_user_event": any(k in event for k in ("user_event", "UserEvent", "userEvent")),
            "raw_preview": event.get("_raw_event_preview", "")[:500],
        }

    candidates = [contest_id]
    if auto_contest:
        candidates = resolve_auto_contest_ids()
        if contest_id not in candidates:
            candidates.insert(0, contest_id)
        report["auto_contest_candidates"] = candidates

    tasks_res = None
    task_list_attempts = []
    for candidate in candidates:
        tasks_url = f"https://atcoder.jp/contests/{candidate}/tasks"
        current = atcoder_get(tasks_url)
        attempt = {
            "contest_id": candidate,
            "url": tasks_url,
            "ok": current.get("ok"),
            "status": current.get("status"),
            "ms": current.get("ms"),
            "bytes": current.get("bytes"),
        }
        task_list_attempts.append(attempt)
        if current.get("ok"):
            contest_id = candidate
            tasks_res = current
            break

    report["contest_id"] = contest_id
    report["task_list_attempts"] = task_list_attempts
    report["task_list"] = task_list_attempts[-1] if task_list_attempts else {}

    if not tasks_res or not tasks_res.get("ok"):
        report["ok"] = False
        report["error"] = "failed to fetch task list"
        report["detail"] = current.get("error") or current.get("body_preview") if task_list_attempts else ""
        return report

    status = load_status(cfg, contest_id)
    status.setdefault("tasks", {})

    task_infos = parse_tasks(contest_id, tasks_res.get("body", ""))
    if target_task_ids:
        task_infos = [x for x in task_infos if x["task_id"] in target_task_ids]

    report["discovered_task_count"] = len(task_infos)

    processed = 0
    for task in task_infos:
        if processed >= max_tasks:
            break
        task_id = task["task_id"]
        task_status = status["tasks"].setdefault(task_id, {})
        task_report = {
            "task_id": task_id,
            "url": task["url"],
            "skipped": False,
        }

        try:
            if task_status.get("sent") and not force:
                task_report["skipped"] = True
                task_report["reason"] = "already sent"
                report["tasks"].append(task_report)
                continue

            fetch_res = atcoder_get(task["url"])
            task_report["fetch"] = {
                "ok": fetch_res.get("ok"),
                "status": fetch_res.get("status"),
                "ms": fetch_res.get("ms"),
                "bytes": fetch_res.get("bytes"),
            }
            if not fetch_res.get("ok"):
                task_status["last_error"] = fetch_res.get("error") or fetch_res.get("body_preview")
                task_report["ok"] = False
                report["ok"] = False
                report["tasks"].append(task_report)
                continue

            page_html = fetch_res["body"]
            title = parse_title(page_html)
            statement = extract_english_statement(page_html)
            if not statement:
                raise RuntimeError("cannot extract English task statement")

            source_hash = sha256_text(statement)
            previous_hash = task_status.get("source_hash")
            previous_translated = task_status.get("translated")
            raw_key = f"{cfg['prefix']}/raw/{contest_id}/{task_id}.html"
            translated_key = f"{cfg['prefix']}/translated/{contest_id}/{task_id}.zh.html"
            meta_key = f"{cfg['prefix']}/translated/{contest_id}/{task_id}.meta.json"
            pdf_key = f"{cfg['prefix']}/pdf/{contest_id}/{task_id}.zh.pdf"

            task_status.update({
                "title": title,
                "url": task["url"],
                "source_hash": source_hash,
                "raw_key": raw_key,
                "translated_key": translated_key,
                "pdf_key": pdf_key,
                "updated_at": now_iso(),
            })

            translated_html = ""
            if previous_translated and previous_hash == source_hash and not force:
                translated_html = oss_get_text(cfg, translated_key, default="")
                translate_meta = {"cached": True}

            if not translated_html:
                oss_put_bytes(cfg, raw_key, page_html.encode("utf-8"), "text/html; charset=utf-8")
                zh_statement, translate_meta = translate_statement(statement)
                translated_html = render_translated_html(
                    contest_id, task_id, title, task["url"], zh_statement, translate_meta,
                )
                oss_put_bytes(cfg, translated_key, translated_html.encode("utf-8"), "text/html; charset=utf-8")
                oss_put_json(cfg, meta_key, {
                    "contest_id": contest_id,
                    "task_id": task_id,
                    "title": title,
                    "url": task["url"],
                    "source_hash": source_hash,
                    "translate_meta": translate_meta,
                    "generated_at": now_iso(),
                })
                task_status["translated"] = True

            task_report["title"] = title
            task_report["source_hash"] = source_hash[:12]
            task_report["translated_key"] = translated_key
            task_report["translate_meta"] = translate_meta

            file_name = f"{task_id}.zh.html"
            file_content = translated_html.encode("utf-8")
            file_content_type = "text/html"

            if output_format == "pdf":
                pdf_bytes = b""
                if task_status.get("pdf") and previous_hash == source_hash and not force:
                    task_report["pdf_cached"] = True
                else:
                    pdf_start = time.perf_counter()
                    pdf_bytes = render_pdf_bytes(translated_html)
                    oss_put_bytes(cfg, pdf_key, pdf_bytes, "application/pdf")
                    task_status["pdf"] = True
                    task_status["pdf_at"] = now_iso()
                    task_report["pdf"] = {
                        "key": pdf_key,
                        "bytes": len(pdf_bytes),
                        "ms": ms_since(pdf_start),
                    }

                if not pdf_bytes:
                    pdf_bytes = oss_get_bytes(cfg, pdf_key, default=b"")
                    if not pdf_bytes:
                        # OSS text helper is UTF-8 oriented; regenerate when cached binary is needed for sending.
                        pdf_start = time.perf_counter()
                        pdf_bytes = render_pdf_bytes(translated_html)
                        task_report["pdf_regenerated_for_send"] = ms_since(pdf_start)

                file_name = f"{task_id}.zh.pdf"
                file_content = pdf_bytes
                file_content_type = "application/pdf"

            if send_enabled:
                result = wecom_send_file(file_name, file_content, file_content_type)
                task_status["sent"] = True
                task_status["sent_at"] = now_iso()
                task_status["wecom_result"] = result
                task_report["sent"] = True
            else:
                task_report["sent"] = False

            task_status.pop("last_error", None)
            task_report["ok"] = True
            processed += 1
            save_status(cfg, contest_id, status)

        except Exception as e:
            task_status["last_error"] = traceback.format_exc()[-1800:]
            task_status["updated_at"] = now_iso()
            task_report["ok"] = False
            task_report["error"] = repr(e)
            report["ok"] = False
            save_status(cfg, contest_id, status)

        report["tasks"].append(task_report)

    report["processed_count"] = processed
    report["total_ms"] = ms_since(started)
    save_status(cfg, contest_id, status)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def task_letter(task_id):
    m = re.search(r"_([a-z])$", task_id, re.I)
    return m.group(1).upper() if m else ""


def allowed_solution_letters():
    raw = env("SOLUTION_TASK_LETTERS", "A,B,C,D,E,F")
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def load_json_object(cfg, key, default=None):
    raw = oss_get_text(cfg, key, default="")
    if not raw:
        return default if default is not None else {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def generate_solution_json(task_id, title, statement_text):
    max_tokens = int(env("OPENAI_SOLUTION_MAX_TOKENS", "7000"))
    solution_model = env("OPENAI_SOLUTION_MODEL") or env("OPENAI_MODEL")
    prompt = (
        "You are a competitive programming teacher writing editorials for Chinese middle-school students.\n"
        "Write a clear, friendly, concise Simplified Chinese solution for the following AtCoder problem.\n"
        "Return ONLY one JSON object with these exact keys:\n"
        "  title: string\n"
        "  overview: string, a short intuitive explanation\n"
        "  algorithm: string, step-by-step algorithm\n"
        "  proof_idea: string, why the algorithm is correct, simple wording\n"
        "  complexity: string, time and memory complexity\n"
        "  cpp17: string, complete accepted C++17 code\n"
        "Rules:\n"
        "- Do not include Markdown fences.\n"
        "- Keep explanations suitable for younger students.\n"
        "- Use standard algorithm-contest C++17 style.\n"
        "- Write math formulas in KaTeX-compatible form, using \\(...\\) for inline math and \\[...\\] for display math.\n"
        "- For example, write \\(O(N \\log N)\\), \\(1 \\le i \\le N\\), and \\(A_i\\).\n"
        "- The code must be complete and include main().\n\n"
        f"Problem id: {task_id}\n"
        f"Problem title: {title}\n"
        "Problem statement:\n"
        + statement_text[:18000]
    )
    raw = openai_generate_text(prompt, max_tokens, model_override=solution_model)
    data = parse_json_object(raw)
    data.setdefault("title", title)
    data.setdefault("overview", "")
    data.setdefault("algorithm", "")
    data.setdefault("proof_idea", "")
    data.setdefault("complexity", "")
    data.setdefault("cpp17", "")
    if env("SOLUTION_REVIEW_PASS", "1") == "1":
        review_prompt = (
            "You are reviewing an AtCoder editorial and C++17 solution for correctness.\n"
            "Check for algorithm mistakes, edge cases, complexity mistakes, and C++ bugs.\n"
            "Return ONLY the corrected JSON object with the same keys:\n"
            "title, overview, algorithm, proof_idea, complexity, cpp17.\n"
            "Keep the explanation in Simplified Chinese for middle-school students.\n"
            "Use KaTeX-compatible math with \\(...\\) or \\[...\\].\n\n"
            "Problem statement:\n"
            + statement_text[:14000]
            + "\n\nDraft JSON:\n"
            + json.dumps(data, ensure_ascii=False)
        )
        reviewed = openai_generate_text(review_prompt, max_tokens, model_override=solution_model)
        data = parse_json_object(reviewed)
        data.setdefault("title", title)
        data.setdefault("overview", "")
        data.setdefault("algorithm", "")
        data.setdefault("proof_idea", "")
        data.setdefault("complexity", "")
        data.setdefault("cpp17", "")
    return data


def html_paragraphs(text):
    text = str(text or "").strip()
    if not text:
        return "<p></p>"
    blocks = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
    if not blocks:
        blocks = [text]
    return "\n".join(
        "<p>" + html.escape(block).replace("\n", "<br>") + "</p>"
        for block in blocks
    )


def render_solutions_html(contest_id, solutions, meta):
    generated_at = html.escape(now_iso())
    title = html.escape(f"{contest_id} A-F Solutions")
    meta_json = html.escape(json.dumps(meta, ensure_ascii=False))
    sections = []
    for sol in solutions:
        task_id = html.escape(sol.get("task_id", ""))
        task_title = html.escape(sol.get("title", ""))
        original_url = html.escape(sol.get("url", ""))
        content = sol.get("solution", {})
        code = html.escape(content.get("cpp17", ""))
        sections.append(f"""
<section class="task-solution">
  <h2>{task_id} - {task_title}</h2>
  <div class="meta"><a href="{original_url}">{original_url}</a></div>
  <h3>Solution Overview</h3>
  {html_paragraphs(content.get("overview", ""))}
  <h3>Algorithm</h3>
  {html_paragraphs(content.get("algorithm", ""))}
  <h3>Correctness Idea</h3>
  {html_paragraphs(content.get("proof_idea", ""))}
  <h3>Complexity</h3>
  {html_paragraphs(content.get("complexity", ""))}
  <h3>C++17 Code</h3>
  <pre><code>{code}</code></pre>
</section>
""")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  {katex_head_html()}
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC",
        "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.68;
      color: #17202a;
      margin: 28px auto;
      max-width: 940px;
      padding: 0 22px 40px;
    }}
    header {{
      border-bottom: 1px solid #d8dee4;
      margin-bottom: 24px;
      padding-bottom: 14px;
    }}
    h1 {{ font-size: 25px; margin: 0 0 8px; }}
    h2 {{
      font-size: 22px;
      margin-top: 34px;
      border-bottom: 1px solid #d8dee4;
      padding-bottom: 8px;
    }}
    h3 {{
      margin-top: 20px;
      font-size: 17px;
      border-left: 4px solid #2f6feb;
      padding-left: 10px;
    }}
    pre {{
      background: #f6f8fa;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      overflow-x: auto;
      padding: 12px;
      line-height: 1.45;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    code {{ font-family: Consolas, "SFMono-Regular", monospace; }}
    .katex {{ font-size: 1.04em; }}
    .katex-display {{ overflow-x: auto; overflow-y: hidden; }}
    .meta {{ color: #57606a; font-size: 13px; }}
    .notice {{
      background: #fff8c5;
      border: 1px solid #eac54f;
      border-radius: 6px;
      padding: 10px 12px;
      margin: 14px 0 20px;
    }}
    .task-solution {{ page-break-before: always; }}
    .task-solution:first-of-type {{ page-break-before: auto; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="meta">Generated: {generated_at}</div>
  </header>
  <div class="notice">Auto-generated editorial for internal study. Please verify with official/editorial sources when needed.</div>
  {''.join(sections)}
  <footer class="meta" data-meta="{meta_json}"></footer>
</body>
</html>
"""


def resolve_task_list_for_event(event, report):
    auto_contest = is_auto_contest_request(event)
    contest_id = get_contest_id(event)
    candidates = [contest_id]
    if auto_contest:
        candidates = resolve_auto_contest_ids()
        if contest_id not in candidates:
            candidates.insert(0, contest_id)
        report["auto_contest_candidates"] = candidates

    tasks_res = None
    task_list_attempts = []
    for candidate in candidates:
        tasks_url = f"https://atcoder.jp/contests/{candidate}/tasks"
        current = atcoder_get(tasks_url)
        attempt = {
            "contest_id": candidate,
            "url": tasks_url,
            "ok": current.get("ok"),
            "status": current.get("status"),
            "ms": current.get("ms"),
            "bytes": current.get("bytes"),
        }
        task_list_attempts.append(attempt)
        if current.get("ok"):
            return candidate, current, task_list_attempts
    return contest_id, tasks_res, task_list_attempts


def run_solutions(event=None):
    started = time.perf_counter()
    cfg = oss_config()
    require_oss_config(cfg)

    force = env("FORCE_REPROCESS", "0") == "1" or env("SOLUTION_FORCE_REPROCESS", "0") == "1"
    send_enabled = env("WECOM_SEND", "0") == "1"
    target_task_ids = get_target_task_ids(event)
    allowed_letters = allowed_solution_letters()

    report = {
        "ok": True,
        "mode": "solutions",
        "image_build_id": env("IMAGE_BUILD_ID", "unknown"),
        "send_enabled": send_enabled,
        "task_letters": sorted(allowed_letters),
        "time": now_iso(),
        "tasks": [],
    }
    if isinstance(event, dict):
        report["event_debug"] = {
            "keys": sorted(str(k) for k in event.keys() if not str(k).startswith("_raw")),
            "contest_id": event.get("contest_id") or event.get("contest"),
            "task_ids": event.get("task_ids"),
            "raw_preview": event.get("_raw_event_preview", "")[:500],
        }

    contest_id, tasks_res, attempts = resolve_task_list_for_event(event, report)
    report["contest_id"] = contest_id
    report["task_list_attempts"] = attempts
    report["task_list"] = attempts[-1] if attempts else {}
    if not tasks_res:
        report["ok"] = False
        report["error"] = "failed to fetch task list"
        return report

    delivery_key = f"{cfg['prefix']}/solutions/{contest_id}/delivery.json"
    delivery = load_json_object(cfg, delivery_key, {})
    if delivery.get("sent") and not force:
        report["skipped"] = True
        report["reason"] = "solutions already sent"
        report["delivery"] = delivery
        report["total_ms"] = ms_since(started)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    task_infos = parse_tasks(contest_id, tasks_res.get("body", ""))
    if target_task_ids:
        task_infos = [x for x in task_infos if x["task_id"] in target_task_ids]
    else:
        task_infos = [x for x in task_infos if task_letter(x["task_id"]) in allowed_letters]

    max_tasks = int(env("SOLUTION_MAX_TASKS", str(len(allowed_letters))))
    task_infos = task_infos[:max_tasks]
    report["discovered_task_count"] = len(task_infos)

    solutions = []
    for task in task_infos:
        task_id = task["task_id"]
        task_report = {"task_id": task_id, "url": task["url"]}
        try:
            page_res = atcoder_get(task["url"])
            task_report["fetch"] = {
                "ok": page_res.get("ok"),
                "status": page_res.get("status"),
                "ms": page_res.get("ms"),
                "bytes": page_res.get("bytes"),
            }
            if not page_res.get("ok"):
                raise RuntimeError(page_res.get("error") or "failed to fetch task page")

            page_html = page_res["body"]
            title = parse_title(page_html)
            statement_html = extract_english_statement(page_html)
            if not statement_html:
                raise RuntimeError("cannot extract English task statement")
            source_hash = sha256_text(statement_html)
            statement_text = html_to_plain_text(statement_html)

            solution_key = f"{cfg['prefix']}/solutions/{contest_id}/{task_id}.json"
            cached = load_json_object(cfg, solution_key, {})
            if cached.get("source_hash") == source_hash and cached.get("solution") and not force:
                solution_obj = cached["solution"]
                task_report["solution_cached"] = True
            else:
                sol_start = time.perf_counter()
                solution_obj = generate_solution_json(task_id, title, statement_text)
                oss_put_json(cfg, solution_key, {
                    "contest_id": contest_id,
                    "task_id": task_id,
                    "title": title,
                    "url": task["url"],
                    "source_hash": source_hash,
                    "solution": solution_obj,
                    "generated_at": now_iso(),
                })
                task_report["solution"] = {"ms": ms_since(sol_start)}

            solutions.append({
                "task_id": task_id,
                "title": title,
                "url": task["url"],
                "solution": solution_obj,
            })
            task_report["ok"] = True
        except Exception as e:
            task_report["ok"] = False
            task_report["error"] = repr(e)
            report["ok"] = False
        report["tasks"].append(task_report)

    if not solutions:
        report["ok"] = False
        report["error"] = "no solutions generated"
        report["total_ms"] = ms_since(started)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    html_key = f"{cfg['prefix']}/solutions/{contest_id}/{contest_id}_solutions_af.zh.html"
    pdf_key = f"{cfg['prefix']}/solutions/{contest_id}/{contest_id}_solutions_af.zh.pdf"
    book_html = render_solutions_html(contest_id, solutions, {
        "contest_id": contest_id,
        "task_count": len(solutions),
        "letters": sorted(allowed_letters),
    })
    oss_put_bytes(cfg, html_key, book_html.encode("utf-8"), "text/html; charset=utf-8")

    pdf_start = time.perf_counter()
    pdf_bytes = render_pdf_bytes(book_html)
    oss_put_bytes(cfg, pdf_key, pdf_bytes, "application/pdf")
    report["combined"] = {
        "html_key": html_key,
        "pdf_key": pdf_key,
        "pdf_bytes": len(pdf_bytes),
        "pdf_ms": ms_since(pdf_start),
    }

    if send_enabled:
        file_name = f"{contest_id}_solutions_A-F.zh.pdf"
        result = wecom_send_file(file_name, pdf_bytes, "application/pdf")
        delivery = {
            "sent": True,
            "sent_at": now_iso(),
            "pdf_key": pdf_key,
            "task_count": len(solutions),
            "wecom_result": result,
        }
        oss_put_json(cfg, delivery_key, delivery)
        report["sent"] = True
    else:
        report["sent"] = False

    report["processed_count"] = len(solutions)
    report["total_ms"] = ms_since(started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def test_atcoder():
    urls = env("ATCODER_TEST_URLS")
    if not urls:
        urls = "https://atcoder.jp/home,https://atcoder.jp/settings"

    cookie, source = get_atcoder_cookie()
    cookie_names = []
    for part in cookie.split(";"):
        part = part.strip()
        if "=" in part:
            cookie_names.append(part.split("=", 1)[0].strip())

    results = {}
    for url in [u.strip() for u in urls.split(",") if u.strip()]:
        res = atcoder_get(url)
        res.pop("body", None)
        res.pop("body_bytes", None)
        results[url] = res

    results["_cookie_diagnostic"] = {
        "source": source,
        "length": len(cookie),
        "names": cookie_names,
        "has_REVEL_SESSION": "REVEL_SESSION" in cookie_names,
        "has_cookie_header": bool(cookie),
    }
    return results


def check_atcoder_session():
    started = time.perf_counter()
    cookie, source = get_atcoder_cookie()
    res = atcoder_get("https://atcoder.jp/settings")
    body = res.get("body", "")
    title = parse_title(body)
    logged_in = (
        res.get("ok")
        and (
            "General Settings - AtCoder" in title
            or "'login_status': 'logged_in'" in body
            or '"login_status": "logged_in"' in body
        )
    )
    return {
        "ok": bool(logged_in),
        "status": res.get("status"),
        "ms": ms_since(started),
        "bytes": res.get("bytes"),
        "title": title,
        "cookie_source": source,
        "cookie_length": len(cookie),
        "has_REVEL_SESSION": "REVEL_SESSION=" in cookie,
        "body_preview": (res.get("body_preview") or res.get("error") or "")[:500],
    }


def run_session_check(event=None):
    started = time.perf_counter()
    result = check_atcoder_session()
    notify_ok = env("SESSION_CHECK_NOTIFY_OK", "0") == "1"
    notify_fail = env("SESSION_CHECK_NOTIFY_FAIL", "1") != "0"

    report = {
        "ok": result["ok"],
        "mode": "session_check",
        "image_build_id": env("IMAGE_BUILD_ID", "unknown"),
        "time": now_iso(),
        "atcoder_session": result,
        "notified": False,
    }

    if result["ok"]:
        if notify_ok:
            wecom_send_text(
                "AtCoder session check OK\n"
                f"time: {now_iso()}\n"
                f"title: {result.get('title')}\n"
                f"latency: {result.get('ms')}ms"
            )
            report["notified"] = True
    elif notify_fail:
        wecom_send_text(
            "AtCoder session check FAILED\n"
            "请在周六比赛前更新 FunctionGraph 环境变量 ATCODER_REVEL_SESSION。\n"
            f"time: {now_iso()}\n"
            f"status: {result.get('status')}\n"
            f"title: {result.get('title')}\n"
            f"has_REVEL_SESSION: {result.get('has_REVEL_SESSION')}"
        )
        report["notified"] = True

    report["total_ms"] = ms_since(started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def test_oss():
    cfg = oss_config()
    require_oss_config(cfg)
    key = f"{cfg['prefix']}/probe/{int(time.time())}-{uuid.uuid4().hex}.json"
    payload = json.dumps({"kind": "functiongraph-probe", "created_at": now_iso()}, ensure_ascii=False).encode("utf-8")
    result = {"key": key}

    start = time.perf_counter()
    put_res = oss_put_bytes(cfg, key, payload, "application/json; charset=utf-8")
    result["put"] = {"ok": True, "status": put_res.get("status"), "ms": ms_since(start)}

    start = time.perf_counter()
    got = oss_get_text(cfg, key, default="")
    result["get"] = {
        "ok": got.encode("utf-8") == payload,
        "ms": ms_since(start),
        "bytes": len(got.encode("utf-8")),
    }

    if env("ALI_OSS_DELETE_PROBE", "1") != "0":
        start = time.perf_counter()
        del_res = oss_request(
            "DELETE", cfg["endpoint"], cfg["bucket"], key,
            cfg["access_key_id"], cfg["access_key_secret"], cfg.get("security_token", ""),
        )
        result["delete"] = {"ok": del_res.get("ok"), "status": del_res.get("status"), "ms": ms_since(start)}

    result["ok"] = result["put"]["ok"] and result["get"]["ok"]
    return result


def test_openai():
    started = time.perf_counter()
    text = openai_generate_text("Reply with exactly one word: pong", 16)
    return {"ok": True, "ms": ms_since(started), "text_preview": text[:80]}


def test_wecom():
    configured = bool(wecom_webhook_key())
    should_send = env("WECOM_PROBE_SEND", "0") == "1"
    if not configured:
        return {"ok": False, "configured": False, "error": "missing WECOM_WEBHOOK_KEY"}
    if not should_send:
        return {
            "ok": True,
            "configured": True,
            "send_enabled": False,
            "note": "set WECOM_PROBE_SEND=1 to send a real probe message",
        }
    started = time.perf_counter()
    result = wecom_send_text(f"AtCoder translator probe OK at {now_iso()}")
    return {"ok": True, "configured": True, "send_enabled": True, "ms": ms_since(started), "result": result}


def run_probe(event=None):
    started = time.perf_counter()
    report = {
        "ok": True,
        "mode": "probe",
        "image_build_id": env("IMAGE_BUILD_ID", "unknown"),
        "time": now_iso(),
        "tests": {},
    }
    if isinstance(event, dict):
        report["event_debug"] = {
            "keys": sorted(str(k) for k in event.keys() if not str(k).startswith("_raw")),
            "contest_id": event.get("contest_id") or event.get("contest"),
            "task_ids": event.get("task_ids"),
            "trigger_type": event.get("trigger_type"),
            "trigger_name": event.get("trigger_name"),
            "has_user_event": any(k in event for k in ("user_event", "UserEvent", "userEvent")),
            "raw_preview": event.get("_raw_event_preview", "")[:500],
        }
    for name, fn in [
        ("atcoder", test_atcoder),
        ("oss", test_oss),
        ("openai", test_openai),
        ("wecom", test_wecom),
    ]:
        try:
            report["tests"][name] = fn()
        except Exception:
            report["tests"][name] = {"ok": False, "error": traceback.format_exc()[-1600:]}

    atcoder_ok = any(x.get("ok") for x in report["tests"].get("atcoder", {}).values())
    report["ok"] = bool(
        atcoder_ok
        and report["tests"].get("oss", {}).get("ok")
        and report["tests"].get("openai", {}).get("ok")
        and report["tests"].get("wecom", {}).get("ok")
    )
    report["total_ms"] = ms_since(started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def handler(event, context):
    try:
        raw_event_preview = str(event)[:1000]
        event = normalize_event(event)
        event["_raw_event_preview"] = raw_event_preview
        mode = env("WORKER_MODE", "worker").lower()
        if isinstance(event, dict) and event.get("mode"):
            mode = str(event["mode"]).lower()
        if mode == "probe":
            return run_probe(event)
        if mode in ("session_check", "check_session", "atcoder_session_check"):
            return run_session_check(event)
        if mode in ("solutions", "solution", "editorial", "editorials"):
            return run_solutions(event)
        return run_worker(event)
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()[-3000:]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report


def probe_handler(event, context):
    return run_probe(event)
