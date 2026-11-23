#!/usr/bin/env python3
"""
Polite, read-only, same-origin crawler for UX site mapping. Python 3.8+ stdlib only
(Playwright is optional and only used with --render).

It only sends GET requests, never submits forms, skips logout/delete-style URLs,
and stays on the starting host. Works against public sites and local dev servers.

Usage:
  python crawl.py http://localhost:3000 --out crawl.json
  python crawl.py https://example.com --max-pages 300 --delay 0.5
  python crawl.py http://localhost:5173 --routes routes.json --render
  SITEMAP_COOKIE="session=abc" python crawl.py http://localhost:3000

Options:
  --max-pages N        stop after N HTML pages (default 300)
  --delay S            seconds between requests (default 0.25; use 0 for localhost)
  --routes FILE        routes.json from discover_routes.py; static routes are added as seeds
  --no-sitemap         don't read /sitemap.xml or robots.txt Sitemap lines
  --ignore-robots      ignore robots.txt (default: obeyed for non-local hosts)
  --keep-query         treat ?query variants as separate pages (default: dropped)
  --exclude REGEX      skip URLs whose path matches REGEX (repeatable)
  --render             render pages with Playwright (for client-rendered SPAs)
  --cookie STRING      Cookie header to send (or set SITEMAP_COOKIE env var)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser

VERSION = "1.0.0"
UA = "ux-sitemap-crawler/%s (+read-only site mapping)" % VERSION
FILE_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|csv|zip|jpe?g|png|gif|webp|avif|svg|ico|css|js|mjs|json|xml|txt|"
    r"mp4|webm|mov|mp3|wav|woff2?|ttf|otf|eot|map)$", re.I)
RISKY = re.compile(r"(log-?out|sign-?out|/delete|/destroy|/remove|unsubscribe|/api/|/_next/|/static/)", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
SPA_ROOT_IDS = {"root", "app", "__next", "__nuxt", "svelte", "___gatsby"}


def is_local(host):
    host = (host or "").lower()
    return (host in ("localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0")
            or host.endswith(".localhost") or host.endswith(".test") or host.endswith(".local"))


def strip_www(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


class PageParser(HTMLParser):
    """Collects title, meta, headings, readable text, links (with page region), forms and ids."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []          # list of (tag, region_marker or None)
        self.title = ""
        self._in_title = False
        self.meta = {}
        self.canonical = None
        self.links = []
        self._link = None
        self.headings = []
        self._heading = None
        self.content_text = []
        self.all_text_len = 0
        self._skip = 0
        self.forms = []
        self._form = None
        self.ids = set()
        self.spa_roots = set()
        self.script_tags = 0
        self.lang = None

    # region of the current position: nav (header/nav), footer, aside or content
    def region(self):
        markers = {m for _, m in self.stack if m}
        if "footer" in markers:
            return "footer"
        if "nav" in markers:
            return "nav"
        if "aside" in markers:
            return "aside"
        return "content"

    @staticmethod
    def _marker(tag, attrs):
        role = (attrs.get("role") or "").lower()
        cls = " " + (attrs.get("class") or "").lower() + " " + (attrs.get("id") or "").lower() + " "
        if tag == "footer" or role == "contentinfo" or re.search(r"[\s_-]footer[\s_-]", cls):
            return "footer"
        if tag in ("header", "nav") or role in ("banner", "navigation") or re.search(r"[\s_-](navbar|site-header|main-nav|w-nav)[\s_-]", cls):
            return "nav"
        if tag == "aside" or role == "complementary":
            return "aside"
        return None

    def handle_starttag(self, tag, attrs_list):
        attrs = {k: (v if v is not None else "") for k, v in attrs_list}
        if attrs.get("id"):
            self.ids.add(attrs["id"])
            if tag == "div" and attrs["id"] in SPA_ROOT_IDS:
                self.spa_roots.add(attrs["id"])
        if attrs.get("name") and tag == "a":
            self.ids.add(attrs["name"])
        if tag == "html" and attrs.get("lang"):
            self.lang = attrs["lang"]
        if tag in ("script", "style", "noscript", "svg", "template"):
            if tag == "script":
                self.script_tags += 1
            self._skip += 1
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key:
                self.meta[key] = attrs.get("content", "")
        elif tag == "link" and "canonical" in (attrs.get("rel") or "").lower():
            self.canonical = attrs.get("href")
        elif tag == "a":
            self._link = {"href": attrs.get("href"), "text": [], "region": self.region(),
                          "label": attrs.get("aria-label") or attrs.get("title") or ""}
        elif tag == "img" and self._link is not None and attrs.get("alt"):
            self._link["text"].append(attrs["alt"])
        elif tag in ("h1", "h2", "h3"):
            self._heading = {"level": int(tag[1]), "text": []}
        elif tag == "form":
            self._form = {"method": (attrs.get("method") or "get").lower(), "action": attrs.get("action") or "",
                          "fields": 0, "region": self.region()}
        elif tag in ("input", "select", "textarea") and self._form is not None:
            if (attrs.get("type") or "").lower() not in ("hidden", "submit", "button"):
                self._form["fields"] += 1
        if tag not in VOID:
            self.stack.append((tag, self._marker(tag, attrs)))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._link is not None:
            text = " ".join(" ".join(self._link["text"]).split()) or self._link["label"].strip()
            self.links.append({"href": self._link["href"], "text": text[:120], "region": self._link["region"]})
            self._link = None
        elif tag in ("h1", "h2", "h3") and self._heading is not None:
            t = " ".join(" ".join(self._heading["text"]).split())
            if t:
                self.headings.append({"level": self._heading["level"], "text": t[:160]})
            self._heading = None
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._link is not None:
            self._link["text"].append(data)
        if self._heading is not None:
            self._heading["text"].append(data)
        stripped = data.strip()
        if stripped:
            self.all_text_len += len(stripped)
            if self.region() == "content":
                self.content_text.append(stripped)


class Crawler:
    def __init__(self, start_url, opts):
        self.opts = opts
        start = urllib.parse.urlsplit(start_url if "://" in start_url else "http://" + start_url)
        self.scheme = start.scheme
        self.host = start.netloc.lower()
        self.host_key = strip_www(start.hostname or "") + (":%d" % start.port if start.port else "")
        self.local = is_local(start.hostname)
        self.base = "%s://%s" % (self.scheme, self.host)
        self.start_key = self.key_for(start_url) or "/"
        self.pages = {}
        self.queue = deque()
        self.queued = set()
        self.warnings = []
        self.robots = None
        self.cookie = opts.cookie or os.environ.get("SITEMAP_COOKIE")
        self.excludes = [re.compile(x) for x in (opts.exclude or [])]
        self.render_ctx = None

    # ---------- URL helpers ----------
    def same_site(self, parts):
        if parts.scheme not in ("http", "https"):
            return False
        host_key = strip_www(parts.hostname or "") + (":%d" % parts.port if parts.port else "")
        return host_key == self.host_key

    def key_for(self, url, base=None):
        """Normalized page key: path (+ query if --keep-query). None if not an internal page URL."""
        if base:
            url = urllib.parse.urljoin(base, url)
        parts = urllib.parse.urlsplit(url)
        if not self.same_site(parts):
            return None
        path = urllib.parse.unquote(parts.path or "/")
        path = re.sub(r"/{2,}", "/", path)
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        if re.search(r"/index\.html?$", path):
            path = path[: path.rfind("/")] or "/"
        key = path or "/"
        if self.opts.keep_query and parts.query:
            key += "?" + parts.query
        return key

    def url_for(self, key):
        return self.base + urllib.parse.quote(key, safe="/?=&%:@+,;~-._")

    def classify_link(self, href, page_url):
        """Return (kind, key_or_url). kinds: internal, file, external, placeholder, anchor, mailto, tel, other"""
        if href is None:
            return "placeholder", ""
        h = href.strip()
        if h in ("", "#", "#!", "javascript:void(0)", "javascript:void(0);", "javascript:;") or h.lower().startswith("javascript:"):
            return "placeholder", h
        if h.startswith("#"):
            return "anchor", h[1:]
        low = h.lower()
        if low.startswith("mailto:"):
            return "mailto", h
        if low.startswith("tel:"):
            return "tel", h
        absolute = urllib.parse.urljoin(page_url, h)
        parts = urllib.parse.urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            return "other", h
        if not self.same_site(parts):
            return "external", absolute.split("#")[0]
        if FILE_EXT.search(parts.path):
            return "file", absolute.split("#")[0]
        key = self.key_for(absolute)
        # same-page link with a fragment counts as an in-page anchor
        if parts.fragment and key == self.key_for(page_url):
            return "anchor", parts.fragment
        return "internal", key

    # ---------- politeness ----------
    def load_robots(self):
        if self.local or self.opts.ignore_robots:
            return
        rp = urllib.robotparser.RobotFileParser()
        try:
            body, status, _, _ = self.http_get(self.base + "/robots.txt", accept="text/plain")
            if status == 200 and body:
                rp.parse(body.splitlines())
                self.robots = rp
        except Exception as e:  # robots is best-effort
            self.warnings.append("robots.txt not read: %s" % e)

    def allowed(self, key):
        if RISKY.search(key):
            return False
        if any(rx.search(key) for rx in self.excludes):
            return False
        if self.robots and not self.robots.can_fetch(UA, self.url_for(key)):
            return False
        return True

    # ---------- fetching ----------
    def http_get(self, url, accept="text/html,application/xhtml+xml"):
        headers = {"User-Agent": UA, "Accept": accept}
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read(6_000_000)
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace"), resp.status, resp.geturl(), ctype
        except urllib.error.HTTPError as e:
            ctype = e.headers.get("Content-Type", "") if e.headers else ""
            try:
                body = e.read(2_000_000).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return body, e.code, url, ctype

    def render_get(self, url):
        if self.render_ctx is None:
            from playwright.sync_api import sync_playwright  # optional dependency
            self._pw = sync_playwright().start()
            browser = self._pw.chromium.launch()
            extra = {"Cookie": self.cookie} if self.cookie else {}
            self.render_ctx = browser.new_context(user_agent=UA, extra_http_headers=extra)
        page = self.render_ctx.new_page()
        try:
            resp = page.goto(url, wait_until="networkidle", timeout=30000)
            status = resp.status if resp else 0
            html = page.content()
            return html, status, page.url, "text/html"
        finally:
            page.close()

    def fetch(self, key):
        url = self.url_for(key)
        if self.opts.render:
            return self.render_get(url)
        return self.http_get(url)

    # ---------- seeds ----------
    def enqueue(self, key, via):
        if key is None:
            return
        if key in self.queued:
            if key in self.pages:
                self.pages[key].setdefault("discovered_via", [])
                if via not in self.pages[key]["discovered_via"]:
                    self.pages[key]["discovered_via"].append(via)
            else:
                for item in self.queue:
                    if item[0] == key and via not in item[1]:
                        item[1].append(via)
            return
        if not self.allowed(key):
            return
        self.queued.add(key)
        self.queue.append((key, [via]))

    def seed_from_sitemaps(self):
        if self.opts.no_sitemap:
            return
        candidates = [self.base + "/sitemap.xml"]
        try:
            body, status, _, _ = self.http_get(self.base + "/robots.txt", accept="text/plain")
            if status == 200:
                candidates += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body)
        except Exception:
            pass
        seen = set()
        while candidates and len(seen) < 20:
            sm = candidates.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            try:
                body, status, _, _ = self.http_get(sm, accept="application/xml,text/xml")
            except Exception:
                continue
            if status != 200 or "<loc>" not in body:
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body):
                loc = loc.replace("&amp;", "&")
                if loc.lower().endswith(".xml"):
                    candidates.append(loc)
                else:
                    self.enqueue(self.key_for(loc), "sitemap.xml")

    def seed_from_routes(self):
        if not self.opts.routes:
            return
        with open(self.opts.routes) as f:
            data = json.load(f)
        for r in data.get("routes", []):
            if not r.get("dynamic"):
                self.enqueue(self.key_for(self.base + r["pattern"]), "source-route")

    # ---------- main loop ----------
    def run(self):
        self.load_robots()
        self.enqueue(self.start_key, "start")
        self.seed_from_routes()
        self.seed_from_sitemaps()
        html_pages = 0
        while self.queue and html_pages < self.opts.max_pages:
            key, via = self.queue.popleft()
            try:
                body, status, final_url, ctype = self.fetch(key)
            except Exception as e:
                self.pages[key] = {"key": key, "url": self.url_for(key), "status": 0, "error": str(e)[:200],
                                   "discovered_via": via}
                continue
            final_key = self.key_for(final_url) or key
            rec = {"key": key, "url": self.url_for(key), "status": status, "content_type": ctype.split(";")[0],
                   "discovered_via": via}
            if final_key != key:
                rec["redirects_to"] = final_key
                self.pages[key] = rec
                self.enqueue(final_key, "redirect")
                continue
            if "html" not in ctype.lower():
                rec["note"] = "non-HTML response"
                self.pages[key] = rec
                continue
            html_pages += 1
            self.pages[key] = self.parse_page(rec, body)
            for link in self.pages[key]["links"]:
                if link["kind"] == "internal":
                    self.enqueue(link["target"], "link")
            if self.opts.delay:
                time.sleep(self.opts.delay)
        if self.queue:
            self.warnings.append("Stopped at --max-pages %d with %d URLs still queued. Re-run with a higher limit "
                                 "or add --exclude patterns." % (self.opts.max_pages, len(self.queue)))
        if self.render_ctx is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        return self.result(html_pages)

    def parse_page(self, rec, body):
        p = PageParser()
        try:
            p.feed(body)
            p.close()
        except Exception as e:
            rec["parse_warning"] = str(e)[:200]
        page_url = rec["url"]
        links = []
        for l in p.links:
            kind, target = self.classify_link(l["href"], page_url)
            item = {"kind": kind, "target": target, "text": l["text"], "region": l["region"]}
            if kind == "anchor":
                item["anchor_found"] = (target in p.ids) or target == "" or target.lower() == "top"
            links.append(item)
        content = " ".join(p.content_text)
        h1 = next((h["text"] for h in p.headings if h["level"] == 1), "")
        spa_shell = bool(p.spa_roots) and p.all_text_len < 200 and p.script_tags > 0
        rec.update({
            "title": " ".join(p.title.split()),
            "meta_description": p.meta.get("description", ""),
            "og_title": p.meta.get("og:title", ""),
            "robots": p.meta.get("robots", ""),
            "canonical": p.canonical,
            "lang": p.lang,
            "h1": h1,
            "headings": p.headings[:25],
            "text_excerpt": content[:1200],
            "word_count": len(content.split()),
            "forms": p.forms,
            "links": links,
            "spa_shell": spa_shell,
        })
        return rec

    def result(self, html_pages):
        if any(p.get("spa_shell") for p in self.pages.values()) and not self.opts.render:
            self.warnings.append("Some pages look like empty client-rendered shells (SPA). Links rendered by "
                                 "JavaScript were not seen. Re-run with --render (needs Playwright) and/or "
                                 "pass --routes from discover_routes.py.")
        return {
            "meta": {
                "tool": "ux-sitemap/crawl.py", "version": VERSION,
                "start_url": self.url_for(self.start_key), "origin": self.base, "local_host": self.local,
                "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "html_pages": html_pages, "urls_recorded": len(self.pages),
                "options": {"max_pages": self.opts.max_pages, "render": self.opts.render,
                            "keep_query": self.opts.keep_query, "robots_obeyed": bool(self.robots),
                            "used_cookie": bool(self.cookie), "routes_file": bool(self.opts.routes)},
                "warnings": self.warnings,
            },
            "pages": self.pages,
        }


def main():
    ap = argparse.ArgumentParser(description="Read-only same-origin crawler for UX site maps")
    ap.add_argument("url")
    ap.add_argument("--out", default="crawl.json")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--delay", type=float, default=None)
    ap.add_argument("--routes")
    ap.add_argument("--no-sitemap", action="store_true")
    ap.add_argument("--ignore-robots", action="store_true")
    ap.add_argument("--keep-query", action="store_true")
    ap.add_argument("--exclude", action="append")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--cookie")
    opts = ap.parse_args()
    if opts.render:
        try:
            import playwright  # noqa: F401
        except ImportError:
            sys.exit("--render needs Playwright: pip install playwright && python -m playwright install chromium")
    crawler = Crawler(opts.url, opts)
    if opts.delay is None:
        opts.delay = 0 if crawler.local else 0.25
    data = crawler.run()
    with open(opts.out, "w") as f:
        json.dump(data, f, indent=1)
    m = data["meta"]
    print("Crawled %d HTML pages (%d URLs recorded) from %s -> %s" % (m["html_pages"], m["urls_recorded"], m["origin"], opts.out))
    for w in m["warnings"]:
        print("WARNING:", w)


if __name__ == "__main__":
    main()
