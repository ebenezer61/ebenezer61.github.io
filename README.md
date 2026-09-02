# ebenezer61.github.io

Personal homepage for Anna.

Live at **<https://ebenezer61.github.io/>**, served by GitHub Pages from the `main` branch.

## Contents

| Path | Purpose |
| --- | --- |
| `index.html` | The homepage: About, Research Interests, Projects, Publications, Teaching & Outreach |
| `style.css` | All styling for the homepage. Light/dark aware, responsive, no external dependencies |
| `interpretability/` | PyCon TW 2026 poster hub: `theory/` (timeline, attention as a kernel, the word "circuit") and `practice/` (the write-up, the graph, the probes) |
| `.nojekyll` | Empty marker telling GitHub Pages to serve the files as-is, without a Jekyll pass |

There is still no build step, no framework, and nothing to install: everything is plain
HTML, CSS and static data, so opening a page in a browser shows exactly what visitors
see.

### `interpretability/`

The hub at <https://ebenezer61.github.io/interpretability/> is the landing page for the
PyCon TW 2026 poster *How LLM reads your Python?* Every page reuses the site's
`style.css` (relative path adjusted for depth) and adds a page-local `<style>` block;
nothing in here affects the rest of the site. The poster's QR codes point at the hub,
`practice/p4/`, `theory/attention/` and `theory/circuits/`, so those four paths should
not move.

| Path | Purpose |
| --- | --- |
| `interpretability/index.html` | Hub: title, abstract, two cards (theory, practice), materials |
| `interpretability/poster.pdf` | The PyCon TW 2026 poster, 78 x 109 cm, built from LaTeX with tectonic |
| `interpretability/theory/index.html` | The timeline 2020 to 2025 in two tracks (how to think, how to look), with SAE and transcoder definitions |
| `interpretability/theory/attention/` | Nadaraya-Watson to softmax attention to QK/OV, one step per screen |
| `interpretability/theory/attention/tensors/` | Shapes, einsum, QK/OV, virtual heads, induction head (the one page that loads KaTeX from a CDN) |
| `interpretability/theory/circuits/` | Where the word "circuit" comes from: Zoom In 2020, the 2021 framework, attribution graphs |
| `interpretability/practice/index.html` | The write-up: prompts, findings, interventions, reproduction steps |
| `interpretability/practice/p4/` | Redirect stub to the pinned P4 graph on Neuronpedia (the QR target; the long URL lives here, not in the QR) |
| `interpretability/practice/py-*_report.json` | The raw attribution reports the tables are built from (14 KB each) |
| `interpretability/practice/traps/`, `gotchas/` | Companion pages: where the binding rules break; the expression is not enough |
| `interpretability/practice/graph/` | Anthropic's attribution-graph viewer (MIT, licence kept), served statically |
| `interpretability/practice/graph/graph_data/` | The pruned P1 attribution graph, 4.5 MB, the largest file in the repository |

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

- **Add a publication**: copy a whole `<li>` block inside `<ol class="entries">` and
  put the new one at the top. For work in progress, write `Under review` or `Preprint`
  where the venue would go.
- **Add a research interest**: copy an `<article class="topic">` block. The grid
  reflows to two or three columns on its own.
- **Add a link to the header**: uncomment one of the examples in `<ul class="links">`
  (GitHub, LinkedIn and CV are already stubbed out there).
- **Add a photo**: drop the image in the repository, then add an `<img>` to the hero.
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
deploy; a hard refresh, or `curl -s https://ebenezer61.github.io/ | grep title`,
will tell you which.

## History

This repository previously hosted a devotional site built on the
[Story](https://html5up.net/story) template by HTML5 UP. It was replaced in August 2026
by the current homepage, and the template's assets (jQuery, Font Awesome, the Unsplash
imagery) were removed at that point. Everything remains in the git history if it is
ever needed again.
