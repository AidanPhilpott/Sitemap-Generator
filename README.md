# ux-sitemap

A Claude Code skill that maps an existing website or web app into a UX site map and packages it as one shareable .zip.

It shows every page with a short description, its type, where it sits in the hierarchy, which pages link to which, and UX issues like orphan pages, broken or placeholder links and missing index pages. It works on local dev servers, staging and public sites, and can read routes from the project's source code (Next.js, Remix, SvelteKit, Nuxt, Astro, React Router and more).

## Install

```bash
git clone https://github.com/AidanPhilpott/Sitemap-Generator ~/.claude/skills/ux-sitemap
```

Requires Python 3.8+. Optional: Playwright, for apps that render pages with JavaScript (`pip install playwright && python -m playwright install chromium`).

## Use

1. Start your dev server.
2. In Claude Code, from your project folder: "Make a UX site map of this app running at http://localhost:3000"
3. For logged-in pages, log in with a test account, copy your session cookie from your browser's dev tools, and run `export SITEMAP_COOKIE="name=value"` before starting Claude Code. Never share a password.
4. Send the resulting `<project>-sitemap-<date>.zip` to your design team.

The crawler is read-only: it only sends GET requests, never submits forms, and skips logout and delete URLs.

## What's in the bundle

| File | What it is |
|---|---|
| `sitemap.html` | Interactive map that opens offline in any browser |
| `pages.csv` | One row per page with description, parent and links |
| `links.csv` | Every connection between pages |
| `issues.csv` | UX findings with severity and recommendations |
| `sitemap.mmd` | Mermaid diagram (paste into FigJam, GitHub or Notion) |
| `sitemap.json` | Full data |
| `README.md` | Summary and hierarchy |
