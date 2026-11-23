# Output formats

## sitemap.json (schema_version 1.0)

```
{
  "schema_version": "1.0",
  "project":  { name, source_url, local_source, crawled_at, generated_at, generator, prepared_by },
  "settings": { depth_ceiling, collapse_threshold, crawl_options, routes_file_used },
  "summary":  { pages, crawled_pages, code_only_pages, templates, max_click_depth,
                pages_by_click_depth, pages_by_type, issues_by_severity,
                global_navigation: [ids], global_footer: [ids],
                global_external_links: [urls]    external URLs linked from most pages (social, app stores),
                sitewide_link_targets: [ids]     pages linked from the content area of most pages (e.g. a CTA),
                crawl_warnings, missing_descriptions },
  "pages": [ {
      id            "P-001", stable across runs when --previous is used
      key           URL path, or pattern like /projects/{id} for templates
      url           absolute URL (null for templates)
      name, type, description, description_status (written | meta-description | missing)
      parent_id, children_ids
      url_depth, click_depth (null = not reachable by links from home)
      nav_placement [header | footer | contextual | unlinked]
      access        public | likely-authenticated | whatever the annotator set
      source        [crawl, code, inferred]
      status, title, h1, meta_description
      inbound_ids, outbound_ids, content_outbound_ids (outbound minus global nav)
      external_links, file_links, forms, notes, issue_ids
      template, instance_count, instance_examples   (templates only)
      source_file   (when found in code)
  } ],
  "edges":  [ { from, to, regions [nav|footer|content|aside], labels [link text], global, tree } ],
  "issues": [ { id, severity high|medium|low, category, message, recommendation, pages [ids] } ]
}
```

`tree: true` means the edge is also the parent→child relationship in the hierarchy.
`global: true` means the link comes from shared header/footer navigation.

## routes.json (from discover_routes.py, or hand-written)

```
{ "meta": {...}, "routes": [ { "pattern": "/projects/{id}", "dynamic": true,
  "framework": "next-app", "source_file": "app/projects/[id]/page.tsx",
  "route_groups": ["(app)"], "access_hint": "likely-authenticated" | "unknown" } ] }
```

## annotations.json

```
{ "pages": { "<key>": { "name", "type", "description", "access", "notes", "_context": {...read only} } } }
```

## CSVs

- pages.csv: id, name, type, url_path, description, parent_id, parent_name, children, click_depth,
  nav_placement, access, links_in, links_out_content, linked_from, links_to, found_via, page_title,
  issues, template_instances, notes
- links.csv: from_id, from_name, to_id, to_name, link_text, where_on_page, is_hierarchy, is_global_nav
- issues.csv: id, severity, category, message, recommendation, page_ids
