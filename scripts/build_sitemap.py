#!/usr/bin/env python3
"""
Turn crawl.json (+ optional routes.json) into a UX site map: hierarchy, page types,
connections, navigation placement and UX issues. Writes sitemap.json.

Two-pass workflow:
  1) python build_sitemap.py --crawl crawl.json [--routes routes.json] --template annotations.json
     -> writes an annotations file with a draft name/type per page and page context,
        and leaves "description" empty for you to write.
  2) python build_sitemap.py --crawl crawl.json [--routes routes.json] --annotations annotations.json \
        --out sitemap.json [--previous old-sitemap.json] [--name "Project"] [--depth-ceiling 4]

--previous keeps page IDs stable across runs (matched by URL path).
"""
import argparse
import json
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"
TYPES = ["entry", "hub", "list", "detail", "content", "funnel", "utility", "legal", "error"]
LEGAL_RX = re.compile(r"(privacy|legal|terms|cookie|gdpr|accessibility|imprint|disclaimer|policy)", re.I)
FUNNEL_RX = re.compile(r"(contact|sign-?up|register|join|checkout|cart|apply|onboard|get-started|demo|book|quote|subscribe)", re.I)
UTILITY_RX = re.compile(r"(log-?in|sign-?in|account|settings|preferences|profile|password|notifications|search|billing)", re.I)
ERROR_RX = re.compile(r"(^|/)(404|500|error|not-found|maintenance)($|/)", re.I)
LIST_RX = re.compile(r"(^|/)(blog|news|work|projects|products|articles|posts|case-studies|jobs|job-listings|careers|events|resources|stories|insights|library|catalog|shop)$", re.I)


def load(path):
    with open(path) as f:
        return json.load(f)


def segs(key):
    return [s for s in key.split("?")[0].split("/") if s]


def pattern_rx(pattern):
    out = "^"
    for s in segs(pattern):
        if s.startswith("{") and s.endswith("*}"):
            out += "(?:/.*)?"
        elif s.startswith("{"):
            out += "/[^/]+"
        else:
            out += "/" + re.escape(s)
    return re.compile((out or "^") + "/?$")


def short_title(title, site_suffixes):
    t = title or ""
    for sep in (" | ", " – ", " — ", " - ", " :: ", " · "):
        if sep in t:
            parts = [p.strip() for p in t.split(sep)]
            parts = [p for p in parts if p.lower() not in site_suffixes] or parts
            t = parts[0]
            break
    return t.strip()


def humanize(key):
    if key == "/":
        return "Home"
    last = segs(key)[-1]
    last = re.sub(r"[{}*]", "", last)
    return re.sub(r"[-_]+", " ", last).strip().capitalize()


def norm_words(s):
    return set(re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("’", "").replace("'", "")).split()) - {
        "the", "a", "an", "and", "of", "our", "us", "to", "for", "your", "with", "in", "page"}


class Builder:
    def __init__(self, args):
        self.a = args
        self.crawl = load(args.crawl)
        self.routes = load(args.routes)["routes"] if args.routes else []
        self.ann = load(args.annotations) if args.annotations else {}
        self.ann = self.ann.get("pages", self.ann)
        self.prev = load(args.previous) if args.previous else None
        self.raw = self.crawl["pages"]

    # ---------------- pages ----------------
    def collect(self):
        raw = self.raw
        ok = {k: p for k, p in raw.items() if p.get("status") == 200 and "links" in p}
        self.redirects = {k: p["redirects_to"] for k, p in raw.items() if p.get("redirects_to")}
        self.errors = {k: p for k, p in raw.items() if p.get("status", 0) >= 400 or p.get("status") == 0}

        # templates: dynamic routes from code collapse their crawled instances
        self.template_of = {}
        templates = {}
        for r in self.routes:
            if not r["dynamic"]:
                continue
            rx = pattern_rx(r["pattern"])
            inst = sorted(k for k in ok if rx.match(k) and not any(k == rr["pattern"] for rr in self.routes))
            if True:  # code says this is a template: always one node, however many instances were crawled
                templates[r["pattern"]] = {"route": r, "instances": inst}
                for k in inst:
                    self.template_of[k] = r["pattern"]
        # heuristic collapse without routes: many leaf siblings under one parent
        by_parent = defaultdict(list)
        for k in ok:
            if k != "/" and k not in self.template_of:
                by_parent["/" + "/".join(segs(k)[:-1]) if len(segs(k)) > 1 else "/"].append(k)
        for parent, kids in by_parent.items():
            if parent == "/" or len(kids) < self.a.collapse_threshold:
                continue
            leaves = [k for k in kids if not any(o.startswith(k + "/") for o in ok)]
            if len(leaves) >= self.a.collapse_threshold:
                pat = (parent if parent != "/" else "") + "/{slug}"
                templates[pat] = {"route": None, "instances": sorted(leaves)}
                for k in leaves:
                    self.template_of[k] = pat
        self.templates = templates

        pages = {}
        for k, p in ok.items():
            if k in self.template_of:
                continue
            pages[k] = {"key": k, "source": ["crawl"], "raw": p}
        for pat, t in templates.items():
            ex = self.raw[t["instances"][0]] if t["instances"] else None
            pages[pat] = {"key": pat, "source": (["code"] if t["route"] else []) + (["crawl"] if t["instances"] else []),
                          "raw": ex, "template": True, "instances": t["instances"],
                          "route": t["route"]}
        # code-only static routes
        for r in self.routes:
            if r["dynamic"] or r["pattern"] in pages:
                if r["pattern"] in pages and "code" not in pages[r["pattern"]]["source"]:
                    pages[r["pattern"]]["source"].append("code")
                    pages[r["pattern"]]["route"] = r
                continue
            gate = self.login_gate(r["pattern"])
            if r["pattern"] in self.redirects and not gate:
                continue
            pages[r["pattern"]] = {"key": r["pattern"], "source": ["code"], "raw": None, "route": r,
                                   "uncrawled": True, "status": raw.get(r["pattern"], {}).get("status"),
                                   "login_gate": gate}
        # crawled URLs that bounced to a login page are real pages behind auth
        for k, tgt in self.redirects.items():
            if k not in pages and self.login_gate(k) and not any(pattern_rx(rr["pattern"]).match(k) for rr in self.routes if rr["dynamic"]):
                pages[k] = {"key": k, "source": ["crawl"], "raw": None, "uncrawled": True, "login_gate": self.login_gate(k)}
        if "/" not in pages:
            pages["/"] = {"key": "/", "source": ["inferred"], "raw": None, "uncrawled": True}
        self.pages = pages

    def login_gate(self, key):
        """The login page a URL redirects to, if it does (i.e. the page needs a session)."""
        tgt = self.redirects.get(key)
        if tgt and re.search(r"(log-?in|sign-?in|auth|sso)", tgt, re.I) and not re.search(r"(log-?in|sign-?in)", key, re.I):
            return tgt
        return None

    def resolve(self, key):
        if key in getattr(self, "pages", {}) and self.pages[key].get("login_gate"):
            return key
        seen = 0
        while key in self.redirects and seen < 10:
            key = self.redirects[key]
            seen += 1
        if key in self.template_of:
            return self.template_of[key]
        return key

    # ---------------- edges ----------------
    def build_edges(self):
        edges = {}
        self.placeholders = defaultdict(list)    # page -> [text]
        self.bad_anchors = defaultdict(list)
        self.broken = defaultdict(list)          # target -> [(source, text)]
        self.redirect_links = defaultdict(set)
        self.files = defaultdict(list)
        self.external = defaultdict(list)
        n_pages = 0
        nav_count, foot_count = defaultdict(int), defaultdict(int)
        for k, pg in self.pages.items():
            p = pg.get("raw")
            if not p or "links" not in p:
                continue
            if pg.get("template") and p["key"] != pg["instances"][0]:
                continue
            n_pages += 1
            seen_nav, seen_foot = set(), set()
            for l in p["links"]:
                kind = l["kind"]
                if kind == "placeholder":
                    self.placeholders[k].append(l["text"] or "(no text)")
                    continue
                if kind == "anchor":
                    if not l.get("anchor_found", True):
                        self.bad_anchors[k].append("#" + l["target"] + (" (%s)" % l["text"] if l["text"] else ""))
                    continue
                if kind == "file":
                    self.files[k].append({"url": l["target"], "text": l["text"]})
                    continue
                if kind == "external":
                    self.external[k].append({"url": l["target"], "text": l["text"], "region": l["region"]})
                    continue
                if kind != "internal":
                    continue
                tgt_raw = l["target"]
                if tgt_raw in self.redirects:
                    self.redirect_links[(k, tgt_raw)].add(self.resolve(tgt_raw))
                if tgt_raw in self.errors and tgt_raw not in self.pages:
                    self.broken[tgt_raw].append((k, l["text"]))
                    continue
                tgt = self.resolve(tgt_raw)
                if tgt not in self.pages or tgt == k:
                    continue
                e = edges.setdefault((k, tgt), {"from": k, "to": tgt, "regions": set(), "texts": set()})
                e["regions"].add(l["region"])
                if l["text"]:
                    e["texts"].add(l["text"])
                if l["region"] == "nav":
                    seen_nav.add(tgt)
                if l["region"] == "footer":
                    seen_foot.add(tgt)
            for t in seen_nav:
                nav_count[t] += 1
            for t in seen_foot:
                foot_count[t] += 1
        thr = max(2, int(0.6 * n_pages)) if n_pages > 2 else 1
        # Apps often have more than one shared header (marketing site vs. logged-in app). Group pages by
        # the set of nav links they carry and treat links shared by most pages in a group as global.
        nav_sets = defaultdict(list)
        for k, pg in self.pages.items():
            p = pg.get("raw")
            if p and "links" in p:
                targets = frozenset(self.resolve(l["target"]) for l in p["links"] if l["kind"] == "internal" and l["region"] == "nav")
                if targets:
                    nav_sets[targets].append(k)
        for targets, members in nav_sets.items():
            if len(members) >= 2 and len(members) >= 0.15 * n_pages:
                for t in targets:
                    if t in self.pages and nav_count[t] >= 2:
                        nav_count[t] = max(nav_count[t], thr)
        ext_count = defaultdict(int)
        for k, lst in self.external.items():
            for u in {e["url"] for e in lst}:
                ext_count[u] += 1
        self.global_external = sorted(u for u, c in ext_count.items() if c >= thr)
        self.global_nav = {t for t, c in nav_count.items() if c >= thr}
        self.global_footer = {t for t, c in foot_count.items() if c >= thr}
        for e in edges.values():
            e["global"] = (e["regions"] <= {"nav", "footer"}) and (e["to"] in self.global_nav | self.global_footer)
        # a page linked from the content of most pages (a repeated "contact us" call to action) is a
        # site-wide destination; its links are kept in the data but not drawn as individual lines
        content_count = defaultdict(int)
        for e in edges.values():
            if not e["global"]:
                content_count[e["to"]] += 1
        # pages already in the shared header/footer, or linked from the content of 40%+ of pages
        self.sitewide_targets = {t for t, c in content_count.items()
                                 if n_pages >= 4 and (c >= max(3, 0.4 * n_pages) or (t in self.global_nav | self.global_footer and c >= 3))}
        for e in edges.values():
            e["sitewide"] = (not e["global"]) and e["to"] in self.sitewide_targets
        self.edges = edges
        self.crawled_count = n_pages
        # most common nav label per globally linked page (what users see in navigation)
        self.nav_label = {}
        for (src, tgt), e in edges.items():
            if tgt in self.global_nav and "nav" in e["regions"]:
                for t in e["texts"]:
                    if t and t not in ("/",) and len(t) < 40:
                        self.nav_label.setdefault(tgt, defaultdict(int))[t] += 1
        self.nav_label = {k: max(v, key=v.get) for k, v in self.nav_label.items()}

    # ---------------- hierarchy ----------------
    def hierarchy(self):
        pages, edges = self.pages, self.edges
        inbound = defaultdict(set)
        outbound = defaultdict(set)
        for (s, t), e in edges.items():
            outbound[s].add(t)
            inbound[t].add(s)
        self.inbound, self.outbound = inbound, outbound
        # click depth via BFS from root
        depth = {"/": 0}
        q = deque(["/"])
        while q:
            u = q.popleft()
            for v in sorted(outbound[u]):
                if v not in depth:
                    depth[v] = depth[u] + 1
                    q.append(v)
        self.click_depth = depth
        parent, missing_parent = {}, set()
        for k in pages:
            if k == "/":
                continue
            s = segs(k)
            par = None
            for i in range(len(s) - 1, 0, -1):
                cand = "/" + "/".join(s[:i])
                if cand in pages:
                    par = cand
                    break
            if par is None and len(s) > 1:
                missing_parent.add(k)
            if par is None and len(s) > 1:
                # no URL ancestor page: use the shallowest page that links here from its content
                linkers = [src for src in inbound[k] if not edges[(src, k)]["global"] and src != k]
                linkers = [x for x in linkers if not (x.startswith(k + "/"))]
                # prefer the page that lists the most pages sharing this URL prefix (the de facto index),
                # then the shallowest one
                linkers.sort(key=lambda x: (-len([o for o in outbound[x] if segs(o)[:-1] == s[:-1]]), depth.get(x, 99), x))
                par = linkers[0] if linkers else "/"
            parent[k] = par or "/"
        self.parent, self.missing_parent = parent, missing_parent
        children = defaultdict(list)
        for k, p in parent.items():
            children[p].append(k)
        self.children = children

    # ---------------- typing & naming ----------------
    def infer_type(self, k, pg):
        p = pg.get("raw") or {}
        text = " ".join([k, p.get("title", ""), p.get("h1", "")])
        if k == "/":
            return "entry"
        if ERROR_RX.search(k):
            return "error"
        if LEGAL_RX.search(k) or (LEGAL_RX.search(p.get("title", "")) and len(segs(k)) == 1 and not self.children.get(k)):
            return "legal"
        if pg.get("template"):
            return "detail"
        forms = [f for f in p.get("forms", []) if f.get("region") == "content" and f.get("fields", 0) >= 2]
        if FUNNEL_RX.search(k):
            return "funnel"
        if UTILITY_RX.search(k):
            return "utility"
        if forms and not self.children.get(k):
            return "funnel"
        kids = self.children.get(k, [])
        linked_kids = [c for c in kids if c in self.outbound[k]]
        if kids and (len(linked_kids) >= 2 or any(self.pages[c].get("template") for c in linked_kids)):
            return "list" if LIST_RX.search(k) else "hub"
        if self.parent.get(k) and LIST_RX.search(self.parent.get(k, "")):
            return "detail"
        if len(segs(k)) >= 2:
            return "detail"
        return "content"

    def draft_name(self, k, pg):
        p = pg.get("raw") or {}
        if pg.get("template"):
            return humanize(self.parent.get(k, "/")) + " detail (template)"
        if k == "/":
            return "Home"
        if k in self.nav_label:
            return self.nav_label[k]
        return short_title(p.get("title", ""), self.suffixes) or p.get("h1") or humanize(k)

    def site_suffixes(self):
        counts = defaultdict(int)
        for pg in self.pages.values():
            t = (pg.get("raw") or {}).get("title", "")
            for sep in (" | ", " – ", " — ", " - ", " · "):
                if sep in t:
                    for part in t.split(sep)[1:]:
                        counts[part.strip().lower()] += 1
        n = max(1, len(self.pages))
        return {s for s, c in counts.items() if c >= max(2, 0.3 * n)}

    # ---------------- IDs ----------------
    def assign_ids(self, order):
        ids = {}
        used = set()
        if self.prev:
            for p in self.prev.get("pages", []):
                ids[p["key"]] = p["id"]
                used.add(p["id"])
        n = max([int(i.split("-")[1]) for i in used] + [0])
        for k in order:
            if k not in ids:
                n += 1
                ids[k] = "P-%03d" % n
        self.retired = sorted(set(ids) - set(self.pages)) if self.prev else []
        return {k: ids[k] for k in order}

    def nav_order(self):
        """Position of each page in the shared header then footer, as it appears on the home page."""
        home = (self.pages.get("/", {}).get("raw") or {})
        order = []
        for region in ("nav", "footer"):
            for l in home.get("links", []):
                if l["kind"] == "internal" and l["region"] == region:
                    t = self.resolve(l["target"])
                    if t not in order and (t in self.global_nav or t in self.global_footer):
                        order.append(t)
        return {k: i for i, k in enumerate(order)}

    def order(self):
        out = []
        pos = self.nav_order()

        def visit(k):
            out.append(k)
            for c in sorted(self.children.get(k, []), key=lambda x: (pos.get(x, 999), self.click_depth.get(x, 99), x)):
                visit(c)
        visit("/")
        for k in sorted(self.pages):
            if k not in out:
                out.append(k)
        return out

    # ---------------- issues ----------------
    def issues(self, ids, records):
        I = []

        def add(sev, cat, msg, rec, pages=()):
            I.append({"severity": sev, "category": cat, "pages": [ids[p] for p in pages if p in ids],
                      "message": msg, "recommendation": rec})

        n = self.crawled_count
        ceiling = self.a.depth_ceiling
        crawled = {k for k, pg in self.pages.items() if pg.get("raw")}

        if any((pg.get("raw") or {}).get("spa_shell") for pg in self.pages.values()):
            add("high", "crawl-coverage", "Pages returned an empty JavaScript shell, so links rendered client-side were not seen.",
                "Re-run the crawl with --render, and run discover_routes.py on the project so code routes are included.")
        for w in self.crawl["meta"].get("warnings", []):
            if "max-pages" in w:
                add("medium", "crawl-coverage", w, "Raise --max-pages or exclude repetitive URL patterns.")

        for k in self.pages:
            if k == "/":
                continue
            if not self.inbound[k] and k in crawled:
                add("high", "orphan", "%s has no links pointing to it from any crawled page." % k,
                    "Link it from its parent hub or navigation, or remove it if it is obsolete.", [k])
        gated = [k for k, pg in self.pages.items() if pg.get("login_gate")]
        if gated:
            add("medium", "login-required", "%d page(s) redirect to %s, so their content and links were not mapped: %s" % (
                len(gated), self.pages[gated[0]]["login_gate"], ", ".join(gated[:12]) + (" ..." if len(gated) > 12 else "")),
                "Re-run the crawl with SITEMAP_COOKIE set to a test account's session to map the logged-in area.", gated)
        code_only = [k for k, pg in self.pages.items() if pg.get("uncrawled") and k != "/" and not pg.get("login_gate")]
        if code_only:
            add("medium", "unreached-route",
                "%d route(s) exist in code but were not reached by the crawl: %s" % (len(code_only), ", ".join(code_only[:12]) + (" ..." if len(code_only) > 12 else "")),
                "If they need login, re-run with a session cookie. Otherwise they may be orphans or unfinished.", code_only)
        for tgt, srcs in self.broken.items():
            status = self.errors[tgt].get("status")
            src_pages = sorted({s for s, _ in srcs})
            add("high", "broken-link", "Links to %s return %s (from %d page(s): %s)." % (tgt, status or "no response", len(src_pages), ", ".join(src_pages)[:200]),
                "Fix or remove the link, or restore the page.", src_pages)

        # placeholder links: separate site-wide (template) ones from page-specific ones
        text_pages = defaultdict(set)
        for k, texts in self.placeholders.items():
            for t in set(texts):
                text_pages[t].add(k)
        sitewide = {t for t, ks in text_pages.items() if n > 2 and len(ks) >= 0.6 * n}
        for t in sorted(sitewide):
            add("medium", "placeholder-link", "Link \"%s\" points to # on %d of %d pages (likely in a shared header, footer or banner)." % (t, len(text_pages[t]), n),
                "Point it at the real destination or remove it.", sorted(text_pages[t])[:1])
        for k, texts in sorted(self.placeholders.items()):
            local = sorted({t for t in texts if t not in sitewide})
            if local:
                add("medium", "placeholder-link", "%s has %d link(s) that go nowhere (#): %s" % (k, len(local), "; ".join(local[:8])),
                    "Give each a destination, or make it a non-link element if it is not meant to navigate.", [k])
        for k, a in sorted(self.bad_anchors.items()):
            add("low", "broken-anchor", "%s links to in-page anchor(s) that don't exist: %s" % (k, ", ".join(sorted(set(a))[:6])),
                "Match the link to an element id on the page.", [k])

        groups = defaultdict(list)
        for k in sorted(self.missing_parent):
            groups["/" + "/".join(segs(k)[:-1])].append(k)
        for par_path, ks in sorted(groups.items()):
            shown = sorted({self.parent[k] for k in ks})
            add("medium", "missing-index", "%d page(s) live under %s, but %s is not a page, so the URL and the navigation disagree. They are shown under %s, the page that links to them: %s." % (
                len(ks), par_path, par_path, ", ".join(shown), ", ".join(ks)),
                ("Add an index page at %s, or move these URLs under %s." % (par_path, shown[0])) if shown[0] != "/" else
                ("Add an index page at %s that lists them, or move them under an existing section." % par_path), ks)
        for k, par in self.parent.items():
            if k in self.missing_parent or par == "/" or k not in crawled or par not in crawled:
                continue
            if k not in self.outbound[par]:
                add("medium", "not-linked-from-parent", "%s is not linked from its parent %s." % (k, par),
                    "Users browsing %s can't reach it. Add a link or reconsider the URL." % par, [k])

        for k, d in self.click_depth.items():
            if d > ceiling and k in ids:
                add("medium", "depth", "%s is %d clicks from Home (ceiling %d)." % (k, d, ceiling),
                    "Surface it from a higher-level hub or navigation.", [k])

        for k in crawled:
            r = records[k]
            if r["type"] in ("legal", "funnel", "error", "utility"):
                continue
            outs = [t for t in self.outbound[k] if not self.edges[(k, t)]["global"]]
            if not outs and k != "/":
                add("low", "dead-end", "%s only links back out through global navigation." % k,
                    "Add a next step: related pages, a next item, or a call to action.", [k])

        # sibling navigation fragmentation (e.g. 'next project' links forming separate loops)
        for par, kids in self.children.items():
            kids = [c for c in kids if c in crawled and not self.pages[c].get("template")]
            if len(kids) < 3:
                continue
            adj = defaultdict(set)
            linked = False
            for a in kids:
                for b in kids:
                    if a != b and (a, b) in self.edges and not self.edges[(a, b)]["global"]:
                        adj[a].add(b)
                        adj[b].add(a)
                        linked = True
            if not linked:
                continue
            comps, seen = [], set()
            for c in kids:
                if c in seen or not adj[c]:
                    continue
                comp, stack = set(), [c]
                while stack:
                    x = stack.pop()
                    if x in comp:
                        continue
                    comp.add(x)
                    stack.extend(adj[x] - comp)
                seen |= comp
                comps.append(sorted(comp))
            if len(comps) > 1:
                add("low", "sibling-navigation", "Pages under %s link to each other in %d separate groups: %s." % (par, len(comps), " | ".join(" + ".join(c) for c in comps)),
                    "Chain them into one sequence so browsing next/previous reaches every sibling.", [x for c in comps for x in c])

        titles = defaultdict(list)
        no_title, no_desc, noindex = [], [], []
        for k in crawled:
            t = (self.pages[k]["raw"] or {}).get("title", "")
            if t:
                titles[t].append(k)
            raw = self.pages[k]["raw"] or {}
            if not t:
                no_title.append(k)
            elif not raw.get("meta_description"):
                no_desc.append(k)
            if raw.get("robots") and "noindex" in raw["robots"].lower():
                noindex.append(k)
        for lst, what, rec in ((no_title, "have no <title>", "Add a distinct, descriptive title to each page."),
                               (no_desc, "have no meta description", "Add a one-sentence description to public pages for search and link previews; app pages behind login can skip this."),
                               (noindex, "are marked noindex", "Confirm that is intentional.")):
            if lst:
                add("low", "metadata", "%d page(s) %s: %s" % (len(lst), what, ", ".join(sorted(lst)[:15]) + (" ..." if len(lst) > 15 else "")), rec, sorted(lst))
        for t, ks in titles.items():
            if len(ks) > 1:
                add("low", "duplicate-title", "%d pages share the title \"%s\"." % (len(ks), t), "Give each page a distinct title.", ks)

        # nav label vs page title
        for k in self.global_nav:
            labels = set()
            for (s, t), e in self.edges.items():
                if t == k and "nav" in e["regions"]:
                    labels |= e["texts"]
            title = records[k]["title"] if k in records else ""
            short = short_title(title, self.suffixes)
            labels = {l for l in labels if norm_words(l)}
            if k == "/":
                continue
            if labels and short and not any(norm_words(l) & norm_words(short) for l in labels):
                add("low", "label-mismatch", "Navigation calls %s \"%s\" but its page title is \"%s\"." % (k, sorted(labels)[0], short),
                    "Use the same name in navigation, page title and H1 so people know they landed in the right place.", [k])

        for (src, tgt), finals in sorted(self.redirect_links.items()):
            add("low", "redirect-link", "%s links to %s, which redirects to %s." % (src, tgt, ", ".join(sorted(finals))),
                "Link straight to the final URL.", [src])

        origin_host = re.sub(r"^https?://", "", self.crawl["meta"]["origin"]).lower()
        canon_hosts = set()
        for k in crawled:
            c = (self.pages[k]["raw"] or {}).get("canonical")
            if c and c.startswith("http"):
                canon_hosts.add(re.sub(r"^https?://([^/]+).*$", r"\1", c).lower())
        if canon_hosts and origin_host not in canon_hosts and not self.crawl["meta"].get("local_host"):
            add("low", "canonical", "Canonical URLs point to %s while the site was served from %s." % (", ".join(sorted(canon_hosts)), origin_host),
                "Confirm a permanent redirect between the two hosts.")

        sev_rank = {"high": 0, "medium": 1, "low": 2}
        I.sort(key=lambda x: (sev_rank[x["severity"]], x["category"], x["message"]))
        for i, issue in enumerate(I, 1):
            issue["id"] = "I-%03d" % i
        return I

    # ---------------- assemble ----------------
    def run(self):
        self.collect()
        self.build_edges()
        self.hierarchy()
        self.suffixes = self.site_suffixes()
        order = self.order()
        ids = self.assign_ids(order)

        records = {}
        for k in order:
            pg = self.pages[k]
            p = pg.get("raw") or {}
            a = self.ann.get(k, {})
            title = p.get("title", "") if not pg.get("template") else ""
            draft_name = self.draft_name(k, pg)
            ptype = a.get("type") if a.get("type") in TYPES else self.infer_type(k, pg)
            desc = (a.get("description") or "").strip()
            nav = []
            if k in self.global_nav:
                nav.append("header")
            if k in self.global_footer:
                nav.append("footer")
            if not nav:
                nav.append("contextual" if self.inbound[k] else "unlinked")
            route = pg.get("route") or {}
            rec = {
                "id": ids[k], "key": k, "url": (self.crawl["meta"]["origin"] + k) if not pg.get("template") else None,
                "name": a.get("name") or draft_name, "type": ptype,
                "description": desc or p.get("meta_description", "") or "",
                "description_status": "written" if desc else ("meta-description" if p.get("meta_description") else "missing"),
                "parent_id": ids.get(self.parent.get(k)) if k != "/" else None,
                "children_ids": [ids[c] for c in sorted(self.children.get(k, []), key=lambda x: order.index(x))],
                "url_depth": len(segs(k)), "click_depth": self.click_depth.get(k),
                "nav_placement": nav,
                "access": a.get("access") or ("authenticated" if pg.get("login_gate") else ("likely-authenticated" if route.get("access_hint") == "likely-authenticated" else "public")),
                "source": pg["source"], "status": p.get("status") if p else pg.get("status"),
                "title": title, "h1": p.get("h1", ""), "meta_description": p.get("meta_description", ""),
                "inbound_ids": sorted(ids[s] for s in self.inbound[k]),
                "outbound_ids": sorted(ids[t] for t in self.outbound[k]),
                "content_outbound_ids": sorted(ids[t] for t in self.outbound[k] if not self.edges[(k, t)]["global"]),
                "external_links": sorted({e["url"] for e in self.external.get(k, [])} - set(self.global_external))[:40],
                "file_links": self.files.get(k, [])[:20],
                "forms": len(p.get("forms", [])) if p else 0,
                "notes": a.get("notes", ""),
            }
            if pg.get("template"):
                rec["template"] = True
                rec["instance_count"] = len(pg["instances"])
                rec["instance_examples"] = pg["instances"][:5]
            if route:
                rec["source_file"] = route.get("source_file")
            records[k] = rec

        issues = self.issues(ids, records)
        by_id = {r["id"]: r for r in records.values()}
        for iss in issues:
            for pid in iss["pages"]:
                by_id[pid].setdefault("issue_ids", []).append(iss["id"])

        edges = []
        for (s, t), e in sorted(self.edges.items(), key=lambda kv: (ids[kv[0][0]], ids[kv[0][1]])):
            edges.append({"from": ids[s], "to": ids[t], "regions": sorted(e["regions"]),
                          "labels": sorted(e["texts"])[:4], "global": e["global"], "sitewide": e["sitewide"],
                          "tree": self.parent.get(t) == s})
        depth_hist = defaultdict(int)
        for r in records.values():
            if r["click_depth"] is not None:
                depth_hist[r["click_depth"]] += 1
        meta = self.crawl["meta"]
        out = {
            "schema_version": SCHEMA_VERSION,
            "project": {
                "name": self.a.name or re.sub(r"^https?://(www\.)?", "", meta["origin"]),
                "source_url": meta["start_url"], "local_source": meta.get("local_host", False),
                "crawled_at": meta["crawled_at"],
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "generator": "ux-sitemap skill 1.0",
                "prepared_by": self.a.prepared_by or "",
            },
            "settings": {"depth_ceiling": self.a.depth_ceiling, "collapse_threshold": self.a.collapse_threshold,
                         "crawl_options": meta.get("options", {}), "routes_file_used": bool(self.routes)},
            "summary": {
                "pages": len(records), "crawled_pages": self.crawled_count,
                "code_only_pages": sum(1 for r in records.values() if r["source"] == ["code"]),
                "templates": sum(1 for r in records.values() if r.get("template")),
                "max_click_depth": max([d for d in self.click_depth.values()] or [0]),
                "pages_by_click_depth": {str(k): v for k, v in sorted(depth_hist.items())},
                "pages_by_type": {t: sum(1 for r in records.values() if r["type"] == t) for t in TYPES if any(r["type"] == t for r in records.values())},
                "issues_by_severity": {s: sum(1 for i in issues if i["severity"] == s) for s in ("high", "medium", "low")},
                "global_navigation": [ids[k] for k in order if k in self.global_nav],
                "global_footer": [ids[k] for k in order if k in self.global_footer],
                "global_external_links": self.global_external,
                "sitewide_link_targets": [ids[k] for k in order if k in self.sitewide_targets],
                "crawl_warnings": meta.get("warnings", []),
                "missing_descriptions": [r["id"] for r in records.values() if r["description_status"] != "written"],
            },
            "pages": [records[k] for k in order],
            "edges": edges,
            "issues": issues,
        }
        if self.retired:
            out["retired_ids"] = self.retired
        return out

    def template(self):
        self.collect()
        self.build_edges()
        self.hierarchy()
        self.suffixes = self.site_suffixes()
        existing = self.ann if isinstance(self.ann, dict) else {}
        pages = {}
        for k in self.order():
            pg = self.pages[k]
            p = pg.get("raw") or {}
            prev = existing.get(k, {})
            name = self.draft_name(k, pg)
            pages[k] = {
                "name": prev.get("name") or name,
                "type": prev.get("type") or self.infer_type(k, pg),
                "description": prev.get("description", ""),
                "access": prev.get("access", "authenticated" if pg.get("login_gate") else ("likely-authenticated" if (pg.get("route") or {}).get("access_hint") == "likely-authenticated" else "public")),
                "notes": prev.get("notes", ""),
                "_context": {
                    "title": p.get("title", ""), "h1": p.get("h1", ""),
                    "meta_description": p.get("meta_description", ""),
                    "headings": [h["text"] for h in p.get("headings", [])][:10],
                    "excerpt": (p.get("text_excerpt") or "")[:500],
                    "source": pg["source"], "source_file": (pg.get("route") or {}).get("source_file"),
                    "instances": pg.get("instances", [])[:5] if pg.get("template") else None,
                },
            }
        return {"_instructions": "Fill in description for every page (one or two plain sentences: what the page is for and what a visitor can do there). Adjust name/type/access if the draft is wrong. Types: " + ", ".join(TYPES) + ". _context is read-only reference.",
                "pages": pages}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--routes")
    ap.add_argument("--annotations")
    ap.add_argument("--previous")
    ap.add_argument("--template", help="write an annotations template to this path and exit")
    ap.add_argument("--out", default="sitemap.json")
    ap.add_argument("--name")
    ap.add_argument("--prepared-by")
    ap.add_argument("--depth-ceiling", type=int, default=4)
    ap.add_argument("--collapse-threshold", type=int, default=12)
    a = ap.parse_args()
    b = Builder(a)
    if a.template:
        t = b.template()
        with open(a.template, "w") as f:
            json.dump(t, f, indent=1, ensure_ascii=False)
        print("Wrote annotation template for %d pages -> %s" % (len(t["pages"]), a.template))
        return
    data = b.run()
    with open(a.out, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    s = data["summary"]
    print("Site map: %d pages, %d connections, max click depth %d, issues %s -> %s" % (
        s["pages"], len(data["edges"]), s["max_click_depth"], s["issues_by_severity"], a.out))
    if s["missing_descriptions"]:
        print("NOTE: %d page(s) still need a written description: %s" % (len(s["missing_descriptions"]), ", ".join(s["missing_descriptions"])))


if __name__ == "__main__":
    main()
