"""
Blackboard Ultra 课程材料批量下载器 —— 通用版 (适用于其他大学)
Blackboard Ultra course-material downloader — generic edition (other universities)

用法 / Usage:
    pip install playwright
    playwright install chromium
    python blackboard_downloader_generic.py [课程关键字] [--base-url 学校Blackboard网址] [--root 保存目录] [--redownload] [--print-all] [--no-print]

流程 / Flow:
    1. 脚本打开浏览器, 你手动登录 Blackboard (含 MFA), 登录后回到命令行按回车
    2. 脚本遍历所有课程 -> 递归所有内容文件夹 -> 下载全部附件, 保持原有文件夹结构
    3. 下载名单 _manifest.jsonl 记录已下载文件, 之后只下载新文件
    4. 纯文字页面另存为 PDF; 外部链接记录到 _links.txt; SharePoint 文件链接自动下载

问题反馈 / Feedback: https://github.com/zbaalnx/Blackboard-Downloader/issues
仅用于下载你本人有权访问的课程材料, 请遵守学校的使用条款。
For downloading course materials you are entitled to access only; follow your institution's terms of use.
"""

import argparse
import hashlib
import html
import http.cookiejar
import json
import mimetypes
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
import urllib.request
from urllib.parse import unquote, urlparse, parse_qs

from playwright.sync_api import sync_playwright

def _parse_args():
    ap = argparse.ArgumentParser(
        description="Blackboard Ultra 课程材料批量下载器 / course-material downloader",
        epilog="问题反馈 / Feedback: https://github.com/zbaalnx/Blackboard-Downloader/issues",
    )
    ap.add_argument("keyword", nargs="*", help="只处理名称/代码含该关键字的课程 / only courses matching this keyword")
    ap.add_argument("--root", help="保存目录 (默认 ~/Blackboard_Downloads, 也可用环境变量 BB_DOWNLOAD_ROOT) / output folder")
    ap.add_argument("--base-url", help="学校 Blackboard 网址, 如 https://blackboard.xxx.ac.uk (也可用环境变量 BB_BASE_URL) / your Blackboard URL")
    ap.add_argument("--redownload", action="store_true", help="忽略下载名单, 全部重新下载 / ignore manifest, redownload everything")
    ap.add_argument("--print-all", action="store_true", help="所有文档页面都另存为 PDF / save every document page as PDF")
    ap.add_argument("--no-print", action="store_true", help="不生成 PDF / do not save pages as PDF")
    return ap.parse_args()


ARGS = _parse_args()
ROOT = Path(ARGS.root or os.environ.get("BB_DOWNLOAD_ROOT") or (Path.home() / "Blackboard_Downloads")).expanduser()
ROOT.mkdir(parents=True, exist_ok=True)



def _resolve_base() -> str:
    """学校网址优先级: --base-url > 环境变量 BB_BASE_URL > 上次保存的 _config.json > 现场输入"""
    cfg = ROOT / "_config.json"
    base = ARGS.base_url or os.environ.get("BB_BASE_URL")
    if not base and cfg.exists():
        try:
            base = json.loads(cfg.read_text(encoding="utf-8")).get("base_url")
        except (OSError, ValueError):
            base = None
    if not base:
        print("请输入你学校 Blackboard 的网址 (例如 https://blackboard.xxx.ac.uk)")
        print("Enter your institution's Blackboard URL (e.g. https://blackboard.example.edu)")
        base = input("URL >>> ").strip()
    if not re.match(r"^https?://", base, re.I):
        base = "https://" + base
    pu = urlparse(base)
    base = f"{pu.scheme}://{pu.netloc}"
    try:
        cfg.write_text(json.dumps({"base_url": base}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return base


BASE = _resolve_base()
START_URL = f"{BASE}/ultra/course"
PROFILE_DIR = ROOT / "_browser_profile"   # 保存登录状态 (含敏感信息, 不要分享!)
LOG_FILE = ROOT / "download_log.txt"

REQUEST_DELAY = 0.15   # 每次请求间隔(秒), 避免给服务器压力
MAX_NAME_LEN = 80      # 单个文件夹/文件名最大长度, 避免 Windows 路径过长


# ---------------------------------------------------------------- 工具函数
def log(msg: str):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def safe_name(name: str, fallback: str = "unnamed") -> str:
    """把名字处理成 Windows 合法文件名"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).rstrip(". ")
    if not name:
        name = fallback
    if len(name) > MAX_NAME_LEN:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 8:
            name = stem[: MAX_NAME_LEN - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_NAME_LEN]
    return name.rstrip(". ") or fallback


def clean_course_name(name: str) -> str:
    """去掉括号后缀(学年/校区等)和多余标点, 让课程文件夹名更简洁"""
    name = re.sub(r"\s*\([^)]*\)", "", name or "")   # 去掉所有 (...) 
    name = re.sub(r"\s*:\s*", " - ", name)           # "MP3000 : xxx" -> "MP3000 - xxx"
    name = re.sub(r"[,\s]+$", "", name.strip())      # 去掉结尾逗号/空格
    return name


def unique_path(path: Path) -> Path:
    """重名时追加 (1), (2)..."""
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def api_get(ctx, path_or_url: str, params=None, retries=3, timeout=120_000):
    """GET 并返回 (status, response)。自动重试。"""
    url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
    last = None
    for attempt in range(retries):
        try:
            time.sleep(REQUEST_DELAY)
            r = ctx.request.get(url, params=params, timeout=timeout)
            last = r
            if r.status == 429 or r.status >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return r
        except Exception as e:  # 网络抖动
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            log(f"  请求异常({attempt + 1}/{retries}): {msg}")
            time.sleep(2 * (attempt + 1))
    return last


def get_json(ctx, path, params=None):
    r = api_get(ctx, path, params)
    if r is None or not r.ok:
        status = r.status if r else None
        if status not in (400, 404):  # 400/404 对"没有附件"很常见, 不提示
            log(f"  ⚠ 接口读取失败 (HTTP {status}): {str(path).split('?')[0]}")
        return None, status
    try:
        return r.json(), r.status
    except Exception:
        return None, r.status


def paged(ctx, path, params=None):
    """遍历分页接口, yield 每条 result"""
    data, status = get_json(ctx, path, params)
    while data:
        for item in data.get("results", []):
            yield item
        nxt = data.get("paging", {}).get("nextPage")
        if not nxt:
            break
        data, status = get_json(ctx, nxt)


# ---------------------------------------------------------------- 课程列表
def list_courses(ctx):
    courses = []
    seen = set()
    for m in paged(
        ctx,
        "/learn/api/v1/users/me/memberships",
        {"expand": "course", "limit": 200, "includeCount": "true"},
    ):
        c = m.get("course") or {}
        cid = m.get("courseId") or c.get("id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        courses.append(
            {
                "id": cid,
                "code": c.get("courseId", ""),
                "name": c.get("name") or c.get("displayName") or c.get("courseId") or cid,
            }
        )
    return courses


# ---------------------------------------------------------------- 下载逻辑
stats = {"downloaded": 0, "skipped": 0, "failed": 0}


VERBOSE = True   # True: 日志里额外显示每个条目的类型, 以及"没有可下载内容"的条目, 方便排查
EMBED_STUB = "@X@EmbeddedFile.requestUrlStub@X@"


MANIFEST_FILE = ROOT / "_manifest.jsonl"
REDOWNLOAD = ARGS.redownload   # 忽略名单, 全部重新下载
# 纯文字的文档页面(没有文件可下载)怎么处理:
#   missing(默认): 没有可下载内容时, 把页面另存为 PDF
#   all:           所有文档页面都另存为 PDF (--print-all)
#   off:           不生成 PDF (--no-print)
PRINT_MODE = "off" if ARGS.no_print else ("all" if ARGS.print_all else "missing")
pdf_state = {"browser": None}   # 专门用来导出 PDF 的无界面浏览器
manifest = {}          # key -> 记录; key 唯一标识 Blackboard 上的一个文件
claimed_paths = set()  # 本次运行中已被某个文件占用的本地路径


def load_manifest():
    manifest.clear()
    if not MANIFEST_FILE.exists():
        return
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # 损坏的行直接忽略
            if "key" in rec:
                manifest[rec["key"]] = rec


def record(key: str, path: Path, size: int):
    """每成功保存(或确认)一个文件, 立刻追加一行到名单, 中途中断也不会丢"""
    claimed_paths.add(str(path))
    rec = {
        "key": key,
        "path": str(path.relative_to(ROOT)),
        "size": size,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    manifest[key] = rec
    try:
        with open(MANIFEST_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"  ⚠ 无法写入名单: {e}")


local_index = {}   # (文件名小写, 大小) -> [路径...]  当前课程文件夹里实际存在的文件


def build_local_index(course_dir: Path):
    """扫描课程文件夹, 记录里面实际存在的文件, 用来识别被移动过的文件"""
    local_index.clear()
    if not course_dir.exists():
        return
    for f in course_dir.rglob("*"):
        try:
            if f.is_file():
                local_index.setdefault((f.name.lower(), f.stat().st_size), []).append(f)
        except OSError:
            continue


def manifest_hit(key: str) -> bool:
    """名单里有, 且本地文件还在 -> 不再下载。
    本地文件不见了: 先看是不是被移到了课程文件夹的别处, 否则判定为误删, 重新下载补回。"""
    rec = manifest.get(key)
    if rec is None:
        return False
    path = ROOT / rec.get("path", "")
    if path.exists():
        claimed_paths.add(str(path))
        stats["skipped"] += 1
        return True
    for cand in local_index.get((path.name.lower(), rec.get("size")), []):
        if str(cand) not in claimed_paths and cand.exists():
            log(f"  ↪ 文件已被移动, 更新名单: {cand.relative_to(ROOT)}")
            record(key, cand, cand.stat().st_size)
            stats["skipped"] += 1
            return True
    stats["restored"] = stats.get("restored", 0) + 1
    log(f"  ↻ 本地文件缺失, 重新下载: {rec.get('path')}")
    return False


def adopt_existing(key: str, target: Path) -> bool:
    """名单里没有, 但本地该位置已有同名文件(例如旧版本下载的): 直接登记, 不重复下载。
    如果这个路径已被本次运行中的另一个文件占用, 则不采用(说明是同名的不同文件)。"""
    if target.exists() and str(target) not in claimed_paths:
        record(key, target, target.stat().st_size)
        stats["skipped"] += 1
        return True
    return False


def save_file(folder: Path, fname: str, body: bytes):
    """保存文件; 同名且大小一致则不重复写入。成功返回最终路径, 失败返回 None。"""
    target = folder / fname
    if target.exists():
        if target.stat().st_size == len(body):
            stats["skipped"] += 1
            return target
        target = unique_path(target)  # 同名但内容不同, 另存
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError as e:
        stats["failed"] += 1
        log(f"  ✗ 写入失败 ({e}): {target}")
        return None
    stats["downloaded"] += 1
    log(f"  ✓ {target.relative_to(ROOT)}  ({len(body) / 1024:.0f} KB)")
    return target


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def make_cookiejar(ctx):
    """把浏览器里的登录状态(cookie)复制给流式下载使用"""
    jar = http.cookiejar.CookieJar()
    for c in ctx.cookies():
        domain = c["domain"]
        dotted = domain.startswith(".")
        exp = c.get("expires", -1)
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=c["name"], value=c["value"],
            port=None, port_specified=False,
            domain=domain, domain_specified=dotted, domain_initial_dot=dotted,
            path=c.get("path", "/"), path_specified=True,
            secure=bool(c.get("secure")),
            expires=int(exp) if exp and exp > 0 else None,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
        ))
    return jar


def stream_download(ctx, url: str, folder: Path, fname=None, retries=2):
    """大文件专用: 边下载边写入硬盘, 不占内存, 先写 .part 文件, 完整后再改名。
    成功返回保存路径; 失败或返回的是网页则返回 None。"""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(make_cookiejar(ctx))
    )
    for attempt in range(retries + 1):
        tmp = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=120) as resp:
                if resp.headers.get_content_type() == "text/html":
                    log("  · 流式下载返回的是网页, 放弃")
                    return None
                name = safe_name(
                    fname or filename_from_headers(resp.headers), fallback="download.bin"
                )
                total = int(resp.headers.get("Content-Length") or 0)
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / name
                if target.exists():
                    if total and target.stat().st_size == total:
                        stats["skipped"] += 1
                        return target
                    target = unique_path(target)
                tmp = target.with_name(target.name + ".part")
                done, last = 0, time.time()
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 30:
                            pct = f" / {total / 1048576:.0f} MB" if total else ""
                            log(f"    … 已下载 {done / 1048576:.0f} MB{pct}  {name}")
                            last = time.time()
            if total and done != total:
                raise OSError(f"下载不完整 ({done}/{total} 字节)")
            tmp.replace(target)
            stats["downloaded"] += 1
            log(f"  ✓ {target.relative_to(ROOT)}  ({done / 1048576:.0f} MB, 流式)")
            return target
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            log(f"  请求异常(流式 {attempt + 1}/{retries + 1}): {msg}")
            if tmp is not None and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            time.sleep(3 * (attempt + 1))
    return None


def download_attachment(ctx, course_id, content_id, att, folder: Path):
    att_id = att["id"]
    key = f"att:{course_id}/{content_id}/{att_id}"
    fname = safe_name(att.get("fileName"), fallback=f"file_{att_id}")
    if not REDOWNLOAD:
        if manifest_hit(key) or adopt_existing(key, folder / fname):
            return
    url = (
        f"{BASE}/learn/api/public/v1/courses/{course_id}"
        f"/contents/{content_id}/attachments/{att_id}/download"
    )
    r = api_get(ctx, url, timeout=600_000)
    if r is not None and not r.ok:
        stats["failed"] += 1
        log(f"  ✗ 下载失败 (HTTP {r.status}): {fname}")
        return
    saved = None
    if r is not None:
        try:
            saved = save_file(folder, fname, r.body())
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            log(f"  ⚠ 文件太大或读取失败, 改用流式下载: {fname}  ({msg})")
    else:
        log(f"  ⚠ 请求多次失败, 改用流式下载: {fname}")
    if saved is None:
        saved = stream_download(ctx, url, folder, fname)
        if saved is None:
            stats["failed"] += 1
            log(f"  ✗ 下载失败: {fname}")
            return
    record(key, saved, saved.stat().st_size)


def extract_embedded_files(body_html: str):
    """从条目正文 HTML 中提取内嵌文件, 返回 [(文件名或None, 完整URL)]。
    Ultra 文档里直接放进去的文件, 就是以这种方式存放的。"""
    found, seen = [], set()
    if not body_html:
        return found

    def add(name, ref):
        ref = html.unescape(ref or "").replace(EMBED_STUB, "/")
        m = re.search(r"(?:https?://[^/\s\"'<>]+)?/*(bbcswebdav/[^\"'\s<>]+)", ref)
        if not m:
            return
        path = m.group(1)
        key = path.split("?")[0]
        if key in seen:
            return
        seen.add(key)
        if not name:
            last = unquote(key.rsplit("/", 1)[-1])
            name = None if last.startswith("xid-") else last
        found.append((name, f"{BASE}/{path}"))

    # 1) Ultra 文档: data-bbfile 属性里带有 linkName(文件名) 和 resourceUrl(地址)
    for m in re.finditer(r'data-bbfile\s*=\s*(["\'])(.*?)\1', body_html, re.S):
        try:
            info = json.loads(html.unescape(m.group(2)))
        except ValueError:
            continue
        add(info.get("linkName"), info.get("resourceUrl", ""))

    # 2) 其他指向 bbcswebdav 的链接 / 图片
    pattern = (
        r'(?:@X@EmbeddedFile\.requestUrlStub@X@|https?://[^/\s"\'<>]+/)'
        r'(bbcswebdav/[^"\'\s<>]+)'
    )
    for m in re.finditer(pattern, body_html):
        add(None, m.group(0))
    return found


def filename_from_headers(headers) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd)
    return m.group(1).strip() if m else ""


def download_embedded(ctx, course_id, name, url, folder: Path):
    key = f"emb:{course_id}/" + url.split("?")[0].replace(BASE + "/", "")
    pre = safe_name(name, fallback="") if name else ""
    if not REDOWNLOAD:
        if manifest_hit(key):
            return
        if pre and re.search(r"\.[A-Za-z0-9]{2,5}$", pre) and adopt_existing(key, folder / pre):
            return
    r = api_get(ctx, url, timeout=600_000)
    if r is None:
        log(f"  ⚠ 请求多次失败, 改用流式下载: {name or url.split('?')[0]}")
        saved = stream_download(ctx, url, folder, pre or None)
        if saved is None:
            stats["failed"] += 1
            log(f"  ✗ 内嵌文件下载失败: {name or url.split('?')[0]}")
        else:
            record(key, saved, saved.stat().st_size)
        return
    if not r.ok:
        stats["failed"] += 1
        log(f"  ✗ 内嵌文件下载失败 (HTTP {r.status}): {name or url.split('?')[0]}")
        return
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    is_html_name = bool(name) and name.lower().endswith((".html", ".htm"))
    if ctype == "text/html" and (not is_html_name or name.startswith("UNSAFE_HTML")):
        # 多半是 Blackboard 自动生成的网页片段, 或被重定向到登录页, 不是真正的材料
        log(f"  · 跳过(返回的是网页而不是文件): {name or url.split('?')[0]}")
        return
    fname = safe_name(name or filename_from_headers(r.headers), fallback="embedded_file")
    if not re.search(r"\.[A-Za-z0-9]{2,5}$", fname) and ctype not in ("", "application/octet-stream"):
        ext = mimetypes.guess_extension(ctype)   # 例如 image/png -> .png
        if ext:
            fname += ext
    saved = None
    try:
        saved = save_file(folder, fname, r.body())
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        log(f"  ⚠ 文件太大或读取失败, 改用流式下载: {fname}  ({msg})")
        saved = stream_download(ctx, url, folder, fname)
        if saved is None:
            stats["failed"] += 1
            log(f"  ✗ 内嵌文件下载失败: {fname}")
            return
    if saved:
        record(key, saved, saved.stat().st_size)


# ---------------------------------------------------------------- SharePoint 链接
SHAREPOINT_RE = re.compile(r"https?://[A-Za-z0-9.-]+\.sharepoint\.com/[^\"'\s<>)\]]+", re.I)


def sharepoint_guid(url: str):
    """从 SharePoint 链接里取出文件 GUID (sourcedoc 或 UniqueId 参数)"""
    q = {k.lower(): v for k, v in parse_qs(urlparse(url).query).items()}
    for k in ("sourcedoc", "uniqueid"):
        if q.get(k):
            g = unquote(q[k][0]).strip("{} ").lower()
            if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", g):
                return g
    return None


def sharepoint_key_part(url: str) -> str:
    return sharepoint_guid(url) or hashlib.sha1(url.split("#")[0].encode("utf-8")).hexdigest()[:12]


def extract_sharepoint_links(body_html, extra_url=None):
    """找出条目里所有指向 SharePoint 的链接(外部链接条目本身 + 正文里的超链接/嵌入)"""
    cands = [extra_url] if extra_url else []
    if body_html:
        cands += SHAREPOINT_RE.findall(html.unescape(body_html))
    out, seen = [], set()
    for u in cands:
        u = html.unescape(u or "").rstrip(".,;")
        if "sharepoint.com" not in u.lower():
            continue
        k = sharepoint_key_part(u)
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


def sharepoint_download_urls(url: str):
    """生成可直接下载文件的候选地址"""
    pu = urlparse(url)
    host = f"{pu.scheme}://{pu.netloc}"
    guid = sharepoint_guid(url)
    urls = []
    if guid:
        m = re.search(r"(/(?:sites|teams|personal)/[^/?#]+)", pu.path)
        site = m.group(1) if m else ""
        urls.append(f"{host}{site}/_layouts/15/download.aspx?UniqueId={guid}")
    urls.append(url + ("&" if "?" in url else "?") + "download=1")
    return urls


def _finalize_download(tmp: Path, target: Path):
    """.part 文件改成正式文件名; 同名且大小一致则丢弃新文件"""
    if target.exists():
        if target.stat().st_size == tmp.stat().st_size:
            tmp.unlink()
            stats["skipped"] += 1
            return target
        target = unique_path(target)
    tmp.replace(target)
    stats["downloaded"] += 1
    log(f"  ✓ {target.relative_to(ROOT)}  ({target.stat().st_size / 1024:.0f} KB, SharePoint)")
    return target


def _sharepoint_via_request(ctx, dl_url, pre, folder: Path):
    r = api_get(ctx, dl_url, retries=2, timeout=300_000)
    if r is None or not r.ok:
        return None
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "text/html":
        return None   # 多半是被重定向到登录页
    name = safe_name(filename_from_headers(r.headers) or pre, fallback="sharepoint_file")
    if not re.search(r"\.[A-Za-z0-9]{2,5}$", name) and ctype not in ("", "application/octet-stream"):
        name += mimetypes.guess_extension(ctype) or ""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (name + ".part")
    tmp.write_bytes(r.body())
    return _finalize_download(tmp, folder / name)


def _sharepoint_via_browser(ctx, dl_urls, pre, folder: Path):
    """请求方式被拒绝时, 用已登录的浏览器打开链接(会自动完成微软单点登录), 接住它触发的下载"""
    page = ctx.new_page()
    try:
        for u in dl_urls:
            try:
                with page.expect_download(timeout=45_000) as di:
                    try:
                        page.goto(u, wait_until="commit", timeout=45_000)
                    except Exception:
                        pass   # 直接触发下载时 goto 会报 "Download is starting", 正常
                d = di.value
            except Exception:
                continue
            name = safe_name(d.suggested_filename or pre, fallback="sharepoint_file")
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / (name + ".part")
            d.save_as(str(tmp))
            return _finalize_download(tmp, folder / name)
        if "login.microsoftonline.com" in page.url:
            log("  ✗ SharePoint 需要重新登录微软账号")
    finally:
        try:
            page.close()
        except Exception:
            pass
    return None


def download_sharepoint(ctx, course_id, title, url, folder: Path):
    key = f"sp:{course_id}/{sharepoint_key_part(url)}"
    q = {k.lower(): v for k, v in parse_qs(urlparse(url).query).items()}
    hint = unquote(q["file"][0]) if q.get("file") else ""
    pre = safe_name(hint or title, fallback="sharepoint_file")
    shown = hint or title
    if not REDOWNLOAD:
        if manifest_hit(key):
            return
        if hint and adopt_existing(key, folder / pre):
            return
    log(f"  🔗 SharePoint 文件: {shown}")
    dl_urls = sharepoint_download_urls(url)
    saved = None
    try:
        for u in dl_urls:
            saved = _sharepoint_via_request(ctx, u, pre, folder)
            if saved:
                break
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        log(f"  请求方式失败: {msg}")
    if saved is None:
        saved = _sharepoint_via_browser(ctx, dl_urls, pre, folder)
    if saved is None:
        stats["failed"] += 1
        pu = urlparse(url)
        log(f"  ✗ SharePoint 文件下载失败: {shown}  ({pu.netloc}{pu.path[:60]})")
        return
    record(key, saved, saved.stat().st_size)


def has_visible_content(body_html) -> bool:
    """文档正文里有没有实际内容(文字/图片/视频), 空白页面不生成 PDF"""
    raw = body_html or ""
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw))
    return bool(text.strip()) or bool(re.search(r"<(img|iframe|video|audio)\b", raw, re.I))


def save_page_as_pdf(ctx, course_id, doc_id, title, folder: Path, body_html):
    """把文档页面用"打印样式"导出为 PDF, 文件名 = 页面标题。
    相当于: 点右上角打印机图标 -> 另存为 PDF (但不需要弹出系统对话框)。"""
    browser = pdf_state["browser"]
    if browser is None:
        return
    # 正文内容变化(老师修改了页面)时, 名单 key 会变, 从而重新生成并覆盖旧 PDF
    digest = hashlib.sha1((body_html or "").encode("utf-8")).hexdigest()[:10]
    key = f"pdf:{course_id}/{doc_id}:{digest}"
    if not REDOWNLOAD and manifest_hit(key):
        return
    target = folder / (safe_name(title, fallback="document") + ".pdf")
    if str(target) in claimed_paths:   # 同一文件夹里已有另一个同名页面
        target = unique_path(target)
    url = f"{BASE}/ultra/courses/{course_id}/document/{doc_id}?view=content&state=view"
    pctx = None
    try:
        pctx = browser.new_context(
            storage_state=ctx.storage_state(),   # 复制登录状态
            viewport={"width": 1280, "height": 900},
        )
        page = pctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass  # 页面一直有后台请求也没关系, 下面再固定等一会儿
        page.wait_for_timeout(2500)
        if "login.microsoftonline.com" in page.url or "/login" in page.url:
            stats["failed"] += 1
            log(f"  ✗ 页面需要重新登录, 无法导出 PDF: {title}")
            return
        page.emulate_media(media="print")
        folder.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        page.pdf(
            path=str(tmp), format="A4", print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"},
        )
        tmp.replace(target)
        record(key, target, target.stat().st_size)
        stats["pdf"] = stats.get("pdf", 0) + 1
        log(f"  🖨 {target.relative_to(ROOT)}  (页面另存为 PDF)")
    except Exception as e:
        stats["failed"] += 1
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        log(f"  ✗ 页面导出 PDF 失败 [{title}]: {msg}")
    finally:
        if pctx is not None:
            try:
                pctx.close()
            except Exception:
                pass


def process_item(ctx, course_id, base, item, folder: Path, depth: int, links: list,
                 parent_id=None, parent_title=None):
    cid = item["id"]
    title = item.get("title") or cid
    ch = item.get("contentHandler") or {}
    handler = ch.get("id", "")
    has_children = bool(item.get("hasChildren"))
    is_container = has_children or handler in (
        "resource/x-bb-folder", "resource/x-bb-lesson"
    )
    indent = "  " * depth

    if handler == "resource/x-bb-externallink":
        links.append(f"{title}\t{ch.get('url', '')}")

    # 1) 文件夹 / 学习模块 / 带子项的条目: 递归进入 (任意层级的嵌套)
    if is_container:
        sub = folder / safe_name(title, fallback=cid)
        log(f"{indent}📁 {title}" + (f"  [{handler}]" if VERBOSE else ""))
        walk_contents(ctx, course_id, cid, sub, depth + 1, parent_title=title)

    if handler == "resource/x-bb-folder":
        return  # 纯文件夹本身没有附件

    # 2) 普通附件
    att_list = list(paged(ctx, f"{base}/{cid}/attachments"))

    # 3) 正文内嵌的文件 (Ultra 文档页面)
    body = item.get("body")
    if body is None and handler == "resource/x-bb-document":
        detail, _ = get_json(ctx, f"{base}/{cid}")
        body = (detail or {}).get("body")
    embedded = extract_embedded_files(body or "")
    disp_title = parent_title if (title == "ultraDocumentBody" and parent_title) else title
    sp_links = extract_sharepoint_links(
        body or "", ch.get("url") if handler == "resource/x-bb-externallink" else None
    )
    for u in sp_links:   # 同时登记到 _links.txt, 方便核对
        links.append(f"{disp_title}\t{u}")

    total = len(att_list) + len(embedded) + len(sp_links)
    if total <= 1 or title == "ultraDocumentBody":
        dest = folder   # ultraDocumentBody 是 Blackboard 内部名称, 不单独建文件夹
    else:
        dest = folder / safe_name(title, fallback=cid)
    for att in att_list:
        download_attachment(ctx, course_id, cid, att, dest)
    for name, url in embedded:
        download_embedded(ctx, course_id, name, url, dest)
    for u in sp_links:
        download_sharepoint(ctx, course_id, disp_title, u, dest)

    # 4) 纯文字的文档页面: 没有文件可下载时, 把页面另存为 PDF (--print-all: 一律另存)
    want_pdf = (
        PRINT_MODE != "off"
        and handler == "resource/x-bb-document"
        and (total == 0 or PRINT_MODE == "all")
        and has_visible_content(body)
    )
    if want_pdf:
        if title == "ultraDocumentBody" and parent_id:
            # Ultra 文档: 页面地址和标题都属于上一级条目
            doc_id, doc_title = parent_id, parent_title or title
        else:
            doc_id, doc_title = cid, title
        if total == 0 and VERBOSE:
            log(f"{indent}· {doc_title}  [文档页面, 没有可下载的附件] -> 另存为 PDF")
        save_page_as_pdf(ctx, course_id, doc_id, doc_title, folder, body)
    elif VERBOSE and total == 0 and not is_container:
        log(f"{indent}· {title}  [{handler or '?'}]  (没有可下载的附件)")


def walk_contents(ctx, course_id, parent_id, folder: Path, depth=0, parent_title=None):
    base = f"/learn/api/public/v1/courses/{course_id}/contents"
    path = base if parent_id is None else f"{base}/{parent_id}/children"

    links = []
    any_item = False
    for item in paged(ctx, path, {"limit": 200}):
        any_item = True
        try:
            process_item(ctx, course_id, base, item, folder, depth, links,
                         parent_id=parent_id, parent_title=parent_title)
        except Exception as e:  # 单个条目出错, 记录后继续处理同一课程里的其他条目
            stats["failed"] += 1
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            log(f"  ✗ 条目处理出错, 已跳过 [{item.get('title', '?')}]: {msg}")

    if links:
        folder.mkdir(parents=True, exist_ok=True)
        lf = folder / "_links.txt"
        try:
            have = set(lf.read_text(encoding="utf-8").splitlines()) if lf.exists() else set()
        except OSError:
            have = set()
        fresh = [x for x in dict.fromkeys(links) if x not in have]   # 去重, 不再每次重复追加
        if fresh:
            with open(lf, "a", encoding="utf-8") as f:
                f.write("\n".join(fresh) + "\n")

    return any_item


# ---------------------------------------------------------------- 主程序
def main():
    log(f"保存目录: {ROOT}")
    load_manifest()
    log(f"下载名单: 已记录 {len(manifest)} 个文件" + ("  (--redownload: 本次忽略名单)" if REDOWNLOAD else ""))
    with sync_playwright() as p:
        launch_args = dict(
            headless=False, accept_downloads=True,
            viewport={"width": 1200, "height": 800},
        )
        try:
            # 优先使用系统自带的 Edge, 对微软单点登录(SSO)兼容性更好
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR / "edge"), channel="msedge", **launch_args
            )
        except Exception as e:
            log(f"无法启动 Edge ({e}), 改用 Playwright 自带 Chromium")
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR / "chromium"), **launch_args
            )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(START_URL)

        print("=" * 60)
        print("请在弹出的浏览器中手动登录 Blackboard (含 MFA)。")
        print("必须等到浏览器里显示 Blackboard 页面(不是登录页)后,")
        print("再回到这里按回车继续。")
        print("=" * 60)
        while True:
            input("登录完成后按回车 >>> ")
            data, status = get_json(ctx, "/learn/api/v1/users/me")
            if data and data.get("id"):
                log(f"登录验证成功: {data.get('userName', data['id'])}")
                break
            print(f"还没有登录成功(接口返回 {status})。"
                  "请先在浏览器里完成登录, 再按回车。")

        if PRINT_MODE != "off":
            try:
                pdf_state["browser"] = p.chromium.launch(headless=True)
            except Exception as e:
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                log(f"无法启动导出 PDF 的浏览器, 本次不生成 PDF: {msg}")

        courses = list_courses(ctx)
        if not courses:
            log("未获取到任何课程。可能未登录成功, 或学校关闭了该接口。")
            ctx.close()
            return
        log(f"共发现 {len(courses)} 门课程")

        # 可选: 在命令后加课程关键字, 只处理名称/代码含关键字的课程
        keyword = " ".join(ARGS.keyword).strip().lower()
        if keyword:
            courses = [
                c for c in courses
                if keyword in f"{c['name']} {c['code']}".lower()
            ]
            log(f"按关键字 '{keyword}' 筛选后剩 {len(courses)} 门课程")

        used_names = set()
        for i, c in enumerate(courses, 1):
            name = safe_name(clean_course_name(c["name"]), fallback=c["id"])
            if name.lower() in used_names:  # 重名课程追加课程代码
                name = safe_name(f"{name} ({c['code'] or c['id']})")
            used_names.add(name.lower())

            log(f"\n[{i}/{len(courses)}] 课程: {c['name']}")
            course_dir = ROOT / name
            build_local_index(course_dir)
            try:
                has = walk_contents(ctx, c["id"], None, course_dir)
            except Exception as e:  # 单门课程出错不影响其他课程
                stats["failed"] += 1
                log(f"  ✗ 该课程处理出错, 已跳过: {e}")
                continue
            if not has:
                log("  (该课程没有可通过接口读取的内容, 或接口无权限)")

        log(
            f"\n完成: 新下载 {stats['downloaded']}, "
            f"跳过(名单/已存在) {stats['skipped']}, 失败 {stats['failed']}, "
            f"补回缺失 {stats.get('restored', 0)}, 页面存为PDF {stats.get('pdf', 0)}, "
            f"名单共 {len(manifest)} 条"
        )
        ctx.close()


if __name__ == "__main__":
    main()
