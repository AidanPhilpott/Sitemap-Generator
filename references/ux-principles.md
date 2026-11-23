# UX site map principles

Read this before reviewing the annotations and before writing the summary. It explains how
to judge what the scripts found and how to describe pages.

## Contents
1. What a UX site map is for
2. Page types
3. Hierarchy: URL structure vs. how people navigate
4. Writing page names and descriptions
5. Navigation placement
6. What the issue categories mean and when they matter
7. Templates and dynamic pages
8. Things the crawl can't see

## 1. What a UX site map is for

A UX site map shows every place a person can land, how those places are grouped, and how
people move between them. It is not an SEO sitemap.xml. The design team uses it to check
that the structure matches how users think about the product, that nothing is stranded,
and that every page has a clear job. So accuracy beats completeness theatre: a page that
exists only in code, or a link that goes nowhere, is a finding, not noise to hide.

## 2. Page types

Every page gets exactly one type. When the script's guess is wrong, fix it in annotations.

| Type | Use it for | Typical examples |
|---|---|---|
| entry | The root and campaign landing pages | Home, /launch |
| hub | Overview page whose main job is routing to child pages | Dashboard home, Settings index, a product area overview |
| list | A collection of similar items | Blog index, /work, search results, a projects table |
| detail | One item from a collection, or one record | A case study, /projects/{id}, a job listing |
| content | A standalone page people read | About, Our why, Pricing, FAQ |
| funnel | A task with an end state, single or multi step | Contact form, signup, checkout, onboarding, apply |
| utility | Account and tool pages | Login, profile, settings, notifications, search |
| legal | Required policy pages | Privacy, terms, cookies, accessibility statement |
| error | Error and status pages | 404, 500, maintenance, access denied |

Rules of thumb: a page with a form whose submission is the point is a funnel. A page that
mainly lists children is list (same kind of item) or hub (mixed destinations). Marketing
pages that explain something are content.

## 3. Hierarchy: URL structure vs. how people navigate

The script builds the tree from URL paths first, because that is how the product is
organized in code. Where a URL's parent path is not a real page (for example
/job-listings/x when /job-listings doesn't exist), it places the page under the
shallowest page that links to it from page content, and raises a missing-index issue.
That mismatch matters: engineers organize by URL, users organize by navigation, and a gap
between the two is where people get lost.

Depth: use click depth (clicks from home), not URL depth. A ceiling of 4 is the default. A
shallow, broad tree is usually easier to navigate than a deep, narrow one, but more than
roughly 7 to 9 items at one level of primary navigation becomes hard to scan.

Funnel steps form a sequence, not a tree. If a signup has steps /signup/1, /signup/2,
present them as a chain under the funnel's first page.

## 4. Writing page names and descriptions

Names: use what a user would call the page, usually the navigation label or H1, in
sentence case. Strip site suffixes ("| Acme"). Two pages should never share a name.

Descriptions: one or two plain sentences, under about 30 words. Say what the page is for
and what a visitor can do there. Base it on the page's actual content (`_context` in the
annotations file), never on what the page should be.

Good: "Lists four client case studies with a one-line summary each and links to the full
stories."
Good: "Single-step contact form; visitors pick a subject from a dropdown and send a message."
Weak: "This page showcases our amazing work." (marketing copy, says nothing about function)
Weak: "Work page." (restates the name)

If a page is a placeholder, empty, or broken, say so: "Empty shell; renders no content
without JavaScript." For pages found only in code, describe from the source file and mark
the description as such: "From source: account settings form for name and email."

## 5. Navigation placement

- header: linked from the shared header/top navigation on most pages (global nav)
- footer: linked from the shared footer on most pages
- contextual: reachable only from links inside other pages' content
- unlinked: no internal links point to it

Global navigation links are hidden from the "Links to" list on each page and from the
cross-link lines on the map so the real content connections stay readable. They are still
in links.csv with is_global_nav = yes.

## 6. What the issue categories mean and when they matter

| Category | Why it matters | Usually acceptable when |
|---|---|---|
| orphan | Nobody can navigate to it | It's a deliberate landing page reached from ads or email (say so in notes) |
| unreached-route | Exists in code but the crawl never reached it | It's behind login and no session cookie was supplied |
| broken-link | Dead end with an error | Never |
| placeholder-link | Looks clickable but does nothing | It's a JS-only control (should be a button, still worth flagging) |
| broken-anchor | Jump link lands nowhere | Never |
| missing-index | URL implies a parent page that doesn't exist; hierarchy and URL disagree | A deliberate flat URL scheme, documented |
| not-linked-from-parent | Browsing the section never surfaces the page | It's an intentionally unlisted page |
| depth | Too many clicks to reach | Deep reference content with search |
| dead-end | No next step except global nav | Terminal pages (confirmation, legal) are already excluded |
| sibling-navigation | "Next" links split siblings into separate loops | Never ideal |
| label-mismatch | Nav label and page title disagree, users doubt they arrived | Short nav label vs. longer title that clearly matches |
| duplicate-title / metadata | Hard to tell pages apart in tabs, history, search | Low priority for authenticated app pages |
| redirect-link | Links point at URLs that redirect | Low priority |

When summarizing, lead with what affects people's ability to find and complete things
(orphans, broken and placeholder links, unreached routes), then structure (missing
index, depth, sibling navigation), then polish (labels, metadata).

## 7. Templates and dynamic pages

Routes with parameters (/projects/{id}) are templates. One node represents all instances,
with an instance count and examples. Without code routes, the script collapses 12 or more
leaf siblings under one parent into a /parent/{slug} template; lower the threshold with
--collapse-threshold if a collection is smaller but clearly templated. Describe the
template, not one instance.

## 8. Things the crawl can't see

- Pages behind login (unless a session cookie is supplied by the person running it)
- Links rendered only by JavaScript (use --render)
- Modal dialogs, drawers and multi-step flows that don't change the URL
- States: empty, loading, error, permission denied

List anything relevant in the README's notes by adding page notes in annotations, for
example "Edit project opens as a modal from this page; no URL".
