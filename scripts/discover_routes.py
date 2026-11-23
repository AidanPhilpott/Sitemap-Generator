#!/usr/bin/env python3
"""
Find the routes a project defines in code, so pages that a crawler can't reach
(behind login, not linked yet, client-rendered) still appear on the site map.

Usage:
  python discover_routes.py /path/to/project --out routes.json

Detects: Next.js (app + pages routers), Remix flat routes, SvelteKit, Nuxt, Astro,
Gatsby, React Router / Vue Router / Angular route configs (pattern match), and
plain .html sites. Output patterns use {param} for dynamic segments.
Read-only: it only reads files.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

IGNORE_DIRS = {"node_modules", ".git", ".next", ".nuxt", ".svelte-kit", ".output", "dist", "build", "out",
               "coverage", ".turbo", ".vercel", ".cache", "public", "vendor", "__pycache__", ".astro", "storybook-static"}
AUTH_HINT = re.compile(r"(^|/|\()(app|dashboard|account|settings|admin|profile|billing|portal|me|workspace|onboarding|protected|private)(\)|/|$)", re.I)
PUBLIC_AUTH_PAGES = re.compile(r"(^|/)(log-?in|sign-?in|sign-?up|register|forgot-password|reset-password|verify)(/|$)", re.I)
PAGE_EXT = (".tsx", ".jsx", ".ts", ".js", ".mdx", ".md")


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def seg_to_pattern(seg, style):
    """Convert one filesystem segment into a URL segment. Returns None to drop the segment."""
    if style in ("next", "nuxt", "sveltekit", "astro", "gatsby"):
        if seg.startswith("(") and seg.endswith(")"):
            return None                      # route group, not in URL
        if seg.startswith("@"):
            return None                      # parallel route slot
        m = re.fullmatch(r"\[\[\.\.\.(\w+)\]\]", seg) or re.fullmatch(r"\[\.\.\.(\w+)\]", seg)
        if m:
            return "{%s*}" % m.group(1)
        m = re.fullmatch(r"\[\[?(\w+)(?:=\w+)?\]?\]", seg)
        if m:
            return "{%s}" % m.group(1)
        if style == "gatsby":
            m = re.fullmatch(r"\{[\w.]+\}", seg)
            if m:
                return "{slug}"
        return seg
    return seg


def build(segs):
    parts = [s for s in segs if s]
    return "/" + "/".join(parts) if parts else "/"


def add(routes, pattern, file, framework, groups=()):
    pattern = re.sub(r"/{2,}", "/", pattern)
    if len(pattern) > 1 and pattern.endswith("/"):
        pattern = pattern[:-1]
    hint_src = "/".join([pattern] + list(groups))
    routes.append({
        "pattern": pattern,
        "dynamic": "{" in pattern,
        "framework": framework,
        "source_file": file,
        "route_groups": list(groups),
        "access_hint": "likely-authenticated" if AUTH_HINT.search(hint_src) and not PUBLIC_AUTH_PAGES.search(pattern) else "unknown",
    })


def discover(root):
    routes, frameworks = [], set()
    files = list(walk(root))
    rel = lambda p: os.path.relpath(p, root).replace(os.sep, "/")

    for f in files:
        r = rel(f)
        # Next.js app router: app/**/page.tsx (also src/app)
        m = re.match(r"(?:src/)?app/(.*?)(?:/)?page\.(tsx|jsx|ts|js|mdx)$", r)
        if m and not r.startswith("app/routes/"):
            frameworks.add("next-app")
            segs_raw = [s for s in m.group(1).split("/") if s]
            groups = [s for s in segs_raw if s.startswith("(")]
            segs = [seg_to_pattern(s, "next") for s in segs_raw]
            add(routes, build(segs), r, "next-app", groups)
            continue
        # Next.js pages router
        m = re.match(r"(?:src/)?pages/(.*)\.(tsx|jsx|ts|js|mdx|md)$", r)
        if m and os.path.exists(os.path.join(root, "package.json")) and not r.endswith(".vue"):
            path = m.group(1)
            base = path.split("/")[-1]
            if path.startswith("api/") or base.startswith("_") or ".test" in base or ".spec" in base:
                continue
            fw = "astro" if r.startswith("src/pages") and any(x.endswith(".astro") for x in files) else "next-pages"
            if "gatsby" in open(os.path.join(root, "package.json"), errors="ignore").read():
                fw = "gatsby"
            frameworks.add(fw)
            segs = path.split("/")
            if segs[-1] == "index":
                segs = segs[:-1]
            add(routes, build([seg_to_pattern(s, "next") for s in segs]), r, fw)
            continue
        # Astro
        m = re.match(r"src/pages/(.*)\.astro$", r)
        if m:
            frameworks.add("astro")
            segs = m.group(1).split("/")
            if segs[-1] == "index":
                segs = segs[:-1]
            add(routes, build([seg_to_pattern(s, "astro") for s in segs]), r, "astro")
            continue
        # Nuxt
        m = re.match(r"(?:src/)?pages/(.*)\.vue$", r)
        if m:
            frameworks.add("nuxt")
            segs = m.group(1).split("/")
            if segs[-1] == "index":
                segs = segs[:-1]
            add(routes, build([seg_to_pattern(s, "nuxt") for s in segs]), r, "nuxt")
            continue
        # SvelteKit
        m = re.match(r"src/routes/(.*?)(?:/)?\+page\.svelte$", r)
        if m:
            frameworks.add("sveltekit")
            segs_raw = [s for s in m.group(1).split("/") if s]
            groups = [s for s in segs_raw if s.startswith("(")]
            add(routes, build([seg_to_pattern(s, "sveltekit") for s in segs_raw]), r, "sveltekit", groups)
            continue
        # Remix / React Router v7 flat routes: app/routes/products.$id.tsx
        m = re.match(r"app/routes/([^/]+?)(?:/route)?\.(tsx|jsx|ts|js|mdx)$", r)
        if m:
            name = m.group(1)
            if name.startswith("api.") or name.startswith("resources.") or name == "root":
                continue
            frameworks.add("remix")
            segs = []
            for s in re.split(r"(?<!\[)\.(?![^\[]*\])", name):
                if s in ("_index", "index"):
                    continue
                if s.startswith("_"):
                    continue                     # pathless layout
                s = s.rstrip("_")
                if s == "$":
                    segs.append("{splat*}")
                elif s.startswith("$"):
                    segs.append("{%s}" % s[1:])
                elif s.startswith("($") and s.endswith(")"):
                    segs.append("{%s}" % s[2:-1])
                else:
                    segs.append(s.strip("[]"))
            add(routes, build(segs), r, "remix")
            continue

    # Config-based routers (React Router, Vue Router, Angular). Pattern match only.
    code_files = [f for f in files if f.endswith((".tsx", ".jsx", ".ts", ".js", ".vue"))]
    path_rx = [
        re.compile(r"<Route\b[^>]*?\bpath\s*=\s*[{]?\s*[\"'`]([^\"'`]*)[\"'`]"),
        re.compile(r"\bpath\s*:\s*[\"'`]([^\"'`]*)[\"'`]"),
    ]
    found_cfg = False
    for f in code_files:
        r = rel(f)
        if re.search(r"(^|/)(app|src/app|src/routes|pages|src/pages)/", r) and frameworks:
            continue
        try:
            text = open(f, errors="ignore").read()
        except Exception:
            continue
        if not re.search(r"createBrowserRouter|<Routes|<Route\b|createRouter|RouterModule|Routes\s*=|routes\s*:", text):
            continue
        for rx in path_rx:
            for p in rx.findall(text):
                if p.startswith(("http", "./", "../")) or p.endswith((".js", ".ts", ".css")) or " " in p:
                    continue
                if p in ("", "*", "**"):
                    continue
                found_cfg = True
                pat = "/" + p.lstrip("/")
                pat = re.sub(r":(\w+)\??", r"{\1}", pat)
                pat = pat.replace("/*", "/{splat*}")
                add(routes, pat, r, "router-config")
    if found_cfg:
        frameworks.add("router-config (nested paths not resolved; check parents)")

    # Plain static HTML
    if not frameworks:
        for f in files:
            if f.endswith((".html", ".htm")):
                r = rel(f)
                path = re.sub(r"(^|/)index\.html?$", "", r)
                path = re.sub(r"\.html?$", "", path)
                frameworks.add("static-html")
                add(routes, "/" + path, r, "static-html")

    # de-duplicate by pattern, keep first source
    seen, out = set(), []
    for rt in sorted(routes, key=lambda x: (x["pattern"].count("/"), x["pattern"])):
        if rt["pattern"] in seen:
            continue
        seen.add(rt["pattern"])
        out.append(rt)
    return out, sorted(frameworks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--out", default="routes.json")
    a = ap.parse_args()
    if not os.path.isdir(a.project):
        sys.exit("Not a directory: %s" % a.project)
    routes, fws = discover(a.project)
    data = {"meta": {"tool": "ux-sitemap/discover_routes.py", "project_root": os.path.abspath(a.project),
                     "frameworks_detected": fws, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "route_count": len(routes)},
            "routes": routes}
    with open(a.out, "w") as f:
        json.dump(data, f, indent=1)
    print("Found %d routes (%s) -> %s" % (len(routes), ", ".join(fws) or "no framework detected", a.out))
    for r in routes:
        print("  %-40s %s%s" % (r["pattern"], r["source_file"], "  [auth?]" if r["access_hint"] != "unknown" else ""))


if __name__ == "__main__":
    main()
