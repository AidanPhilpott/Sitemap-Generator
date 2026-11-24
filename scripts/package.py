#!/usr/bin/env python3
"""
Package sitemap.json into a hand-off bundle a design team can open without tools.

Usage:
  python package.py sitemap.json --out-dir ./sitemap-handoff

Writes into out-dir and zips it as <slug>-sitemap-<YYYYMMDD>.zip:
  sitemap.html   interactive map (open in any browser, works offline)
  sitemap.json   full machine-readable data (source of truth)
  pages.csv      one row per page (opens in Sheets, Excel, Airtable, FigJam table import)
  links.csv      one row per connection between pages
  issues.csv     UX findings
  sitemap.mmd    Mermaid hierarchy (paste into FigJam, Mermaid Live, GitHub, Notion)
  README.md      summary and how to read the bundle
"""
import argparse
import csv
import json
import os
import re
import sys
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(HERE, "..", "assets", "viewer.html")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "site"


def mermaid(data):
    pages = data["pages"]
    lines = ["flowchart LR"]
    for p in pages:
        label = (p["name"] + "<br/>" + p["key"] + " · " + p["type"]).replace('"', "'")
        lines.append('  %s["%s"]' % (p["id"].replace("-", ""), label))
    for p in pages:
        if p["parent_id"]:
            lines.append("  %s --> %s" % (p["parent_id"].replace("-", ""), p["id"].replace("-", "")))
    cross = [e for e in data["edges"] if not e["tree"] and not e["global"]]
    if cross and len(cross) <= 80:
        lines.append("  %% cross-links between pages (non-hierarchy, non-global-nav)")
        for e in cross:
            lines.append("  %s -.-> %s" % (e["from"].replace("-", ""), e["to"].replace("-", "")))
    types = sorted({p["type"] for p in pages})
    palette = {"entry": "#2f5bd3", "hub": "#7a4cc2", "list": "#7a4cc2", "detail": "#2b8a6e", "content": "#4a7a9c",
               "funnel": "#c2622d", "utility": "#6b6862", "legal": "#8a8577", "error": "#c4372b"}
    for t in types:
        lines.append("  classDef %s stroke:%s,stroke-width:3px" % (t, palette.get(t, "#888")))
        ids = ",".join(p["id"].replace("-", "") for p in pages if p["type"] == t)
        lines.append("  class %s %s" % (ids, t))
    return "\n".join(lines) + "\n"


def write_csvs(data, out):
    by = {p["id"]: p for p in data["pages"]}
    with open(os.path.join(out, "pages.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "type", "url_path", "description", "parent_id", "parent_name", "children",
                    "click_depth", "nav_placement", "access", "links_in", "links_out_content", "linked_from",
                    "links_to", "found_via", "page_title", "issues", "template_instances", "notes"])
        for p in data["pages"]:
            w.writerow([p["id"], p["name"], p["type"], p["key"], p["description"], p["parent_id"] or "",
                        by[p["parent_id"]]["name"] if p["parent_id"] else "", " ".join(p["children_ids"]),
                        "" if p["click_depth"] is None else p["click_depth"], ", ".join(p["nav_placement"]), p["access"],
                        len(p["inbound_ids"]), len(p["content_outbound_ids"]), " ".join(p["inbound_ids"]),
                        " ".join(p["outbound_ids"]), ", ".join(p["source"]), p["title"],
                        " ".join(p.get("issue_ids", [])), p.get("instance_count", ""), p.get("notes", "")])
    with open(os.path.join(out, "links.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "from_name", "to_id", "to_name", "link_text", "where_on_page", "is_hierarchy", "is_global_nav"])
        for e in data["edges"]:
            w.writerow([e["from"], by[e["from"]]["name"], e["to"], by[e["to"]]["name"], " / ".join(e["labels"]),
                        ", ".join(e["regions"]), "yes" if e["tree"] else "no", "yes" if e["global"] else "no"])
    with open(os.path.join(out, "issues.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "severity", "category", "message", "recommendation", "page_ids"])
        for i in data["issues"]:
            w.writerow([i["id"], i["severity"], i["category"], i["message"], i["recommendation"], " ".join(i["pages"])])


def tree_text(data):
    by = {p["id"]: p for p in data["pages"]}
    root = next(p for p in data["pages"] if not p["parent_id"])
    out = []

    def walk(p, pre="", last=True, top=True):
        conn = "" if top else ("└─ " if last else "├─ ")
        out.append("%s%s%s  %s  (%s, %s)" % (pre, conn, p["id"], p["name"], p["key"], p["type"]))
        kids = [by[c] for c in p["children_ids"]]
        for i, k in enumerate(kids):
            walk(k, pre + ("" if top else ("   " if last else "│  ")), i == len(kids) - 1, False)
    walk(root)
    return "\n".join(out)


def readme(data, zipname):
    pr, s = data["project"], data["summary"]
    top = [i for i in data["issues"] if i["severity"] in ("high", "medium")][:12]
    lines = [
        "# Site map: %s" % pr["name"], "",
        "Source: %s  " % pr["source_url"],
        "Crawled: %s%s  " % (pr["crawled_at"], " (local dev server)" if pr.get("local_source") else ""),
        ("Prepared by: %s  " % pr["prepared_by"]) if pr.get("prepared_by") else "",
        "Generated by: %s" % pr["generator"], "",
        "## What's in this bundle", "",
        "| File | Open with | What it is |", "|---|---|---|",
        "| `sitemap.html` | Any browser (offline) | Interactive map: hierarchy, page descriptions, connections, issues |",
        "| `sitemap.json` | Code / re-import | Complete data, the source of truth for every other file |",
        "| `pages.csv` | Sheets, Excel, FigJam | One row per page with description, parent, links in and out |",
        "| `links.csv` | Sheets, Excel | Every connection between two pages and where on the page the link sits |",
        "| `issues.csv` | Sheets, Excel | UX findings with severity and recommendation |",
        "| `sitemap.mmd` | FigJam, Mermaid Live, GitHub, Notion | Diagram of the hierarchy plus content cross-links |", "",
        "## Summary", "",
        "%d pages (%d crawled, %d found only in code, %d templates), %d connections, deepest page %d clicks from home." % (
            s["pages"], s["crawled_pages"], s["code_only_pages"], s["templates"], len(data["edges"]), s["max_click_depth"]),
        "Issues: %d high, %d medium, %d low." % (s["issues_by_severity"]["high"], s["issues_by_severity"]["medium"], s["issues_by_severity"]["low"]), "",
        "## Hierarchy", "", "```", tree_text(data), "```", "",
    ]
    if top:
        lines += ["## Top findings", ""] + ["- **%s** (%s): %s" % (i["id"], i["severity"], i["message"]) for i in top] + [""]
    if s["crawl_warnings"]:
        lines += ["## Crawl notes", ""] + ["- " + w for w in s["crawl_warnings"]] + [""]
    if s["missing_descriptions"]:
        lines += ["## Pages without a written description", "", ", ".join(s["missing_descriptions"]), ""]
    lines += ["## Sending this back", "", "Send `%s` as is. Everything the design team needs is inside it." % zipname, ""]
    return "\n".join(l for l in lines if l is not None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sitemap")
    ap.add_argument("--out-dir", default="sitemap-handoff")
    a = ap.parse_args()
    data = json.load(open(a.sitemap))
    for key in ("schema_version", "project", "pages", "edges", "issues", "summary"):
        if key not in data:
            sys.exit("sitemap.json is missing '%s'. Re-run build_sitemap.py." % key)
    os.makedirs(a.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    zipname = "%s-sitemap-%s.zip" % (slug(data["project"]["name"]), stamp)

    html = open(VIEWER).read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__SITEMAP_DATA__*/null", payload)
    with open(os.path.join(a.out_dir, "sitemap.html"), "w") as f:
        f.write(html)
    with open(os.path.join(a.out_dir, "sitemap.json"), "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    with open(os.path.join(a.out_dir, "sitemap.mmd"), "w") as f:
        f.write(mermaid(data))
    write_csvs(data, a.out_dir)
    with open(os.path.join(a.out_dir, "README.md"), "w") as f:
        f.write(readme(data, zipname))

    zpath = os.path.join(os.path.dirname(os.path.abspath(a.out_dir)), zipname)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in ("README.md", "sitemap.html", "sitemap.json", "pages.csv", "links.csv", "issues.csv", "sitemap.mmd"):
            z.write(os.path.join(a.out_dir, fn), os.path.join(slug(data["project"]["name"]) + "-sitemap", fn))
    print("Bundle: %s" % zpath)
    print("Folder: %s" % os.path.abspath(a.out_dir))


if __name__ == "__main__":
    main()
