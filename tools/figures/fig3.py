import json
D = json.load(open("gen.json"))
ORDER = [("consistent","A bound twice","valid"),
         ("two-valid","both bound, only A grows","valid"),
         ("never-bound","A only ever read","control"),
         ("split","D bound, A grows in loop","control")]
W = 760
LAB = 232
X0, X1 = LAB, 706
PT = 40
ROW = 54
H = PT + ROW*4 + 40
def x(v): return X0 + (v + 5) / 105 * (X1 - X0)

out = []
for t in (0, 25, 50, 75, 100):
    out.append(f'<line x1="{x(t):.1f}" y1="{PT-16}" x2="{x(t):.1f}" y2="{PT+ROW*4-16:.0f}" stroke="var(--viz-grid)" stroke-width="1"/>')
    out.append(f'<text x="{x(t):.1f}" y="{PT-22}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-3)">{t}%</text>')
out.append(f'<line x1="{x(0):.1f}" y1="{PT-16}" x2="{x(0):.1f}" y2="{PT+ROW*4-16:.0f}" stroke="var(--viz-axis)" stroke-width="1.5"/>')

for i,(key,desc,kind) in enumerate(ORDER):
    yy = PT + i*ROW
    rows = D[key]
    hits = sum(1 for r in rows if not r["miss"])
    out.append(f'<text x="{LAB-14}" y="{yy}" text-anchor="end" font-size="12" font-family="ui-monospace, Menlo, monospace" fill="var(--viz-ink-1)">{key}</text>')
    out.append(f'<text x="{LAB-14}" y="{yy+15}" text-anchor="end" font-size="10.5" fill="var(--viz-ink-3)">{desc}</text>')
    tag = "runnable Python" if kind=="valid" else "invalid Python (control)"
    out.append(f'<text x="{LAB-14}" y="{yy+29}" text-anchor="end" font-size="10" fill="var(--viz-ink-3)" font-style="italic">{tag}</text>')
    for r in rows:
        cx, cy = x(r["margin"]), yy+6
        if r["miss"]:
            out.append(f'<g><title>{r["name"]}: P(acc) {r["acc"]:.2f}%, P(ans) {r["distr"]:.2f}% &#8212; exact tie, counted a miss</title>'
                       f'<circle cx="{cx:.1f}" cy="{cy}" r="6.5" fill="var(--viz-surface)" stroke="var(--viz-ink-1)" stroke-width="2"/></g>')
            out.append(f'<text x="{cx+11:.1f}" y="{cy-9}" font-size="10.5" fill="var(--viz-ink-1)">{r["name"]} &#8212; exact tie, counted a miss</text>')
        else:
            out.append(f'<g><title>{r["name"]}: P(acc) {r["acc"]:.2f}%, P(ans) {r["distr"]:.2f}%, margin {r["margin"]:+.2f}</title>'
                       f'<circle cx="{cx:.1f}" cy="{cy}" r="5" fill="var(--viz-s1)" opacity=".78" stroke="var(--viz-surface)" stroke-width="1.5"/></g>')
    out.append(f'<text x="{X1+4}" y="{yy+10}" font-size="11" font-weight="600" fill="var(--viz-ink-1)">{hits}/18</text>')

out.append(f'<text x="{(X0+X1)/2:.0f}" y="{H-14}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-3)">margin: P(name the binding account predicts) &#8722; P(the other name), one dot per accumulator name</text>')
svg = "\n".join("\t\t\t\t\t\t\t" + s for s in out)
open("fig3.svgfrag","w").write(f"{W} {H}\n"+svg)
print("fig3", W, H)
