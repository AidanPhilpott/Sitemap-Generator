---
name: ux-sitemap
description: Map an existing website or web app into a UX site map and package it as a shareable hand-off bundle (interactive HTML map, JSON, CSVs, Mermaid diagram, README in one .zip). Shows every page, a short description of each, its type, where it sits in the hierarchy, and which pages link to which, plus UX issues like orphan pages, dead links, placeholder links and missing index pages. Works on public sites, staging, and local dev servers (localhost), and can read routes from the project's source code (Next.js, Remix, SvelteKit, Nuxt, Astro, React Router and more). Use this whenever someone asks for a site map, sitemap, page inventory, IA audit, page list, route map, "what pages does this app have", "how are the pages connected", or wants to send a design team a picture of an MVP's structure, even if they don't say "UX".
---

# UX site map

Produce a UX site map of an existing site or project and package it so it can be sent to a
design team as a single .zip. The person running this is often an engineer building an MVP,
and the person reading the result is a designer who won't have their environment. So the
bundle has to stand on its own: it opens in a browser offline, every page has a plain
description, and anything the crawl couldn't see is said out loud.

All scripts are Python 3.8+ standard library (Playwright only for `--render`). They live in
this skill's `scripts/` folder; below, `$S` means that folder's path.

## Step 1: Get the inputs

Ask only for what you can't find yourself. Check the repo (package.json scripts, README,
.env.example) for the dev server port before asking.

- **Site URL** (required): a running local dev server (`http://localhost:3000`), staging, or a public URL. If the dev server isn't running and you're in the project, offer to start it (for example `npm run dev`) in the background and wait until it responds.
- **Project folder** (strongly recommended for apps): lets you find routes the crawl can't reach, like pages behind login.
- **Project name** and **who's preparing it**: go on the cover of the bundle.
- **Logged-in pages**: if the app has them, ask the person to copy their own session cookie from their browser's dev tools and set it as an environment variable: `export SITEMAP_COOKIE="name=value"`. Never ask for, type, or store a password, and don't print the cookie. A cookie for a local or test account is ideal.

Only crawl sites the person owns or is responsible for. The crawler is read-only (GET only,
no form submissions, skips logout/delete/API URLs, stays on one host, obeys robots.txt on
non-local hosts), but production sites still deserve the default delay.

## Step 2: Find routes in code (if you have the project)

```bash
python $S/discover_routes.py <project-folder> --out routes.json
```

Look at the output. If it found nothing for an obvious framework, read the router config
yourself and write routes.json by hand in the same shape (see `references/output-schema.md`).
Nested React Router configs are matched by pattern only, so check that child paths include
their parents.

## Step 3: Crawl

```bash
python $S/crawl.py <url> --out crawl.json [--routes routes.json] [--max-pages 300]
```

Read the printed warnings, they change what to do next:
- **Empty JavaScript shell (SPA)**: re-run with `--render` (needs `pip install playwright && python -m playwright install chromium`). If that's not possible, rely on routes.json and say so in notes.
- **Stopped at --max-pages**: raise the limit or add `--exclude` for repetitive URLs (for example `--exclude '^/tags/'`).
- Many 401/403 or login redirects: logged-in pages need `SITEMAP_COOKIE` (Step 1).

## Step 4: Name, type and describe every page

```bash
python $S/build_sitemap.py --crawl crawl.json [--routes routes.json] --template annotations.json
```

Read `references/ux-principles.md` (sections 2 and 4) first. Then open annotations.json.
For each page, using its `_context` (title, headings, excerpt, source file):

- write `description`: one or two plain sentences on what the page is for and what someone can do there. Describe what's actually there, including if it's empty or broken.
- correct `name` (what a user would call it) and `type` (entry, hub, list, detail, content, funnel, utility, legal, error) where the draft is wrong.
- set `access` (public, authenticated, role: admin, and so on) if you know it.
- add `notes` for things the crawl can't see: modals, multi-step flows without URLs, pages reached only from email.

For pages found only in code, read the source file to describe them and start the
description with "From source:". Don't invent pages that aren't in the crawl or the code;
if you know of one, put it in a note on its parent instead.

Every page needs a description. The build prints any that are still missing.

## Step 5: Build the site map

```bash
python $S/build_sitemap.py --crawl crawl.json [--routes routes.json] --annotations annotations.json \
  --out sitemap.json --name "<Project name>" --prepared-by "<name>" [--previous old/sitemap.json]
```

Use `--previous` when re-running for the same project so page IDs (P-001...) stay stable.

## Step 6: Sanity check before packaging

Read sitemap.json's `summary`, the `issues`, and skim the hierarchy (README tree after
Step 7, or print parents yourself). Check against `references/ux-principles.md` section 6:

- Does the tree make sense as a user would see it? If a page landed under the wrong parent because its URL has no index page, that's expected and already flagged; don't hide it.
- Are any issues false alarms you can explain (an orphan that's an ad landing page, an unreached route that needs login)? Put the explanation in that page's `notes` and rebuild. Don't delete findings.
- If a type or name is off, fix annotations and rebuild.

## Step 7: Package and hand off

```bash
python $S/package.py sitemap.json --out-dir sitemap-handoff
```

This writes `<project>-sitemap-<date>.zip` next to the folder, containing sitemap.html
(interactive map), sitemap.json, pages.csv, links.csv, issues.csv, sitemap.mmd and README.md.

Finish by telling the person, briefly:
1. where the .zip is, and that it's the one file to send to the design team;
2. how many pages and connections, and the deepest click depth;
3. the three to five findings that most affect people finding things or finishing tasks;
4. what the map does not cover (logged-out only, no JS rendering, modals, max-pages cut-off).

Keep that summary short and plain. The bundle carries the detail.

## Files

- `scripts/discover_routes.py`: routes from source code
- `scripts/crawl.py`: read-only crawler (static HTML; `--render` for SPAs)
- `scripts/build_sitemap.py`: hierarchy, types, connections, issues; annotation template
- `scripts/package.py`: hand-off bundle
- `assets/viewer.html`: template for the interactive map
- `references/ux-principles.md`: page types, description style, how to judge issues
- `references/output-schema.md`: file formats, for anyone importing the results
