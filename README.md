# ebenezer61.github.io

Personal homepage for Anna.

Live at **<https://ebenezer61.github.io/>**, served by GitHub Pages from the `main` branch.

## Contents

| Path | Purpose |
| --- | --- |
| `index.html` | The homepage — About, Research Interests, Projects, Publications, Teaching & Outreach |
| `style.css` | All styling for the homepage. Light/dark aware, responsive, no external dependencies |
| `interpretability/` | Write-up of the PyCon TW 2026 attribution-graph experiment, plus the graph itself |
| `.nojekyll` | Empty marker telling GitHub Pages to serve the files as-is, without a Jekyll pass |

There is still no build step, no framework, and nothing to install: everything is plain
HTML, CSS and static data, so opening a page in a browser shows exactly what visitors
see.

### `interpretability/`

A standalone page at <https://ebenezer61.github.io/interpretability/> about which
variable `gemma-2-2b` predicts after `return`. It reuses `../style.css` and adds its
own page-local `<style>` block; nothing in it affects the rest of the site.

| Path | Purpose |
| --- | --- |
| `interpretability/index.html` | The write-up: prompt, findings, an inline SVG of the circuit, reproduction steps |
| `interpretability/py-return-variable_report.json` | The raw attribution report the tables are built from (14 KB) |
| `interpretability/graph/` | Anthropic's attribution-graph viewer (MIT, licence kept), served statically |
| `interpretability/graph/graph_data/` | The pruned attribution graph, 4.5 MB — the largest file in the repository. Node labels and the pre-pinned subgraph are added on top of what `circuit-tracer` emits |

The viewer needs a live connection: it loads d3, dagre and pako from
`transformer-circuits.pub` and `unpkg`. The write-up page itself has no external
dependencies and reads fine without them.

## Editing

Everything you would normally want to change lives in `index.html`, and the places
that need your attention are marked with `TODO (Anna)` comments:

```
grep -n "TODO (Anna)" index.html
```

Common edits:

- **Add a publication** — copy a whole `<li>` block inside `<ol class="entries">` and
  put the new one at the top. For work in progress, write `Under review` or `Preprint`
  where the venue would go.
- **Add a research interest** — copy an `<article class="topic">` block. The grid
  reflows to two or three columns on its own.
- **Add a link to the header** — uncomment one of the examples in `<ul class="links">`
  (GitHub, LinkedIn and CV are already stubbed out there).
- **Add a photo** — drop the image in the repository, then add an `<img>` to the hero.
  The portrait styles were removed when the layout went text-only, so this needs a
  little CSS as well.

Colors, fonts, spacing and the max content width are all CSS custom properties at the
top of `style.css`, under `:root` for light mode and the `prefers-color-scheme: dark`
block for dark mode.

## Publishing

Commit and push to `main`; GitHub Pages rebuilds within a minute or two.

```
git add -A
git commit -m "Update publications"
git push
```

If the live page still looks stale, it is browser or CDN caching rather than a failed
deploy — a hard refresh, or `curl -s https://ebenezer61.github.io/ | grep title`,
will tell you which.

## History

This repository previously hosted a devotional site built on the
[Story](https://html5up.net/story) template by HTML5 UP. It was replaced in August 2026
by the current homepage, and the template's assets (jQuery, Font Awesome, the Unsplash
imagery) were removed at that point. Everything remains in the git history if it is
ever needed again.
