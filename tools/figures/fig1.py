# Figure 1 - orthogonality: own-logit drive vs cross-logit drive, 10 pathways
ROWS = [  # (graph, pathway name, own, cross, is_winner)
 ("split-binding",      "result", 26.963, 1.389, True),
 ("split-binding",      "ans",    20.268, 0.049, False),
 ("result-never-bound", "ans",    26.957, 0.700, True),
 ("result-never-bound", "result", 17.027, 0.921, False),
 ("two-valid-locals",   "result", 37.301, 1.996, True),
 ("two-valid-locals",   "ans",    21.295, -0.206, False),
 ("split-total",        "total",  38.883, -0.641, True),
 ("split-total",        "ans",    19.000, 0.060, False),
 ("total-never-bound",  "ans",    28.040, 0.657, True),
 ("total-never-bound",  "total",  20.838, -0.277, False),
]

W, LAB = 760, 208
X0, X1 = LAB, 700          # plot area
VMIN, VMAX = -3.0, 42.0
BAR, GAP, GRP = 10, 4, 12  # bar height, gap between the pair, gap between graphs
TOP = 46

def x(v): return X0 + (v - VMIN) / (VMAX - VMIN) * (X1 - X0)

out, y = [], TOP
ticks = [0, 10, 20, 30, 40]
rows_y = []
prev_graph = None
for g, name, own, cross, win in ROWS:
    if prev_graph is not None and g != prev_graph:
        y += GRP
    rows_y.append((y, g, name, own, cross, win))
    y += BAR * 2 + GAP + 10
    prev_graph = g
H = y + 34

# ---- gridlines + axis
for t in ticks:
    out.append(f'<line x1="{x(t):.1f}" y1="{TOP-14}" x2="{x(t):.1f}" y2="{H-34:.0f}" '
               f'stroke="var(--viz-grid)" stroke-width="1"/>')
    out.append(f'<text x="{x(t):.1f}" y="{TOP-20}" text-anchor="middle" font-size="10.5" '
               f'fill="var(--viz-ink-3)">{t}</text>')
out.append(f'<line x1="{x(0):.1f}" y1="{TOP-14}" x2="{x(0):.1f}" y2="{H-34:.0f}" '
           f'stroke="var(--viz-axis)" stroke-width="1.5"/>')

# ---- the "cross terms live here" band
bx0, bx1 = x(-0.641), x(1.996)
out.append(f'<rect x="{bx0:.1f}" y="{TOP-14}" width="{bx1-bx0:.1f}" height="{H-34-(TOP-14):.0f}" '
           f'fill="var(--viz-s2)" opacity=".12"/>')

seen = set()
for (yy, g, name, own, cross, win) in rows_y:
    if g not in seen:
        seen.add(g)
        out.append(f'<text x="{LAB-12}" y="{yy+10}" text-anchor="end" font-size="11.5" '
                   f'font-family="ui-monospace, Menlo, monospace" fill="var(--viz-ink-1)">{g}</text>')
    lbl = f'{name}{" &#9733;" if win else ""}'
    out.append(f'<text x="{LAB-12}" y="{yy+25}" text-anchor="end" font-size="11" '
               f'fill="var(--viz-ink-2)">{lbl}</text>')
    # own bar
    ow = max(x(own) - x(0), 2)
    out.append(f'<g><title>{g} &#183; {name} nodes &#8594; the &#8220;{name}&#8221; logit: +{own:.3f}</title>'
               f'<rect x="{x(0):.1f}" y="{yy:.0f}" width="{ow:.1f}" height="{BAR}" rx="4" '
               f'fill="var(--viz-s1)"/></g>')
    out.append(f'<text x="{x(own)+7:.1f}" y="{yy+BAR-1:.0f}" font-size="11" '
               f'font-variant-numeric="tabular-nums" fill="var(--viz-ink-1)">+{own:.2f}</text>')
    # cross bar
    cw = x(cross) - x(0)
    cx = x(0) if cw >= 0 else x(cross)
    other = "ans" if name != "ans" else ("total" if "total" in g else "result")
    out.append(f'<g><title>{g} &#183; {name} nodes &#8594; the &#8220;{other}&#8221; logit: {cross:+.3f}</title>'
               f'<rect x="{cx:.1f}" y="{yy+BAR+GAP:.0f}" width="{max(abs(cw),1.5):.1f}" height="{BAR}" '
               f'rx="4" fill="var(--viz-s2)"/></g>')

# band annotation
out.append(f'<text x="{bx1+8:.1f}" y="{H-16}" font-size="10.5" fill="var(--viz-ink-3)">'
           f'every cross term falls in this band: &#8722;0.64 to +2.00</text>')
out.append(f'<line x1="{bx1:.1f}" y1="{H-30:.0f}" x2="{bx1+5:.1f}" y2="{H-20:.0f}" '
           f'stroke="var(--viz-ink-3)" stroke-width="1"/>')

svg = "\n".join("\t\t\t\t\t\t\t" + s for s in out)
print(f'VIEWBOX 0 0 {W} {H}')
open("fig1.svgfrag","w").write(f"{W} {H}\n"+svg)
