# Paired-bar figure: P(correct) vs P(wrong) per case, grouped by pair.
CASES = [  # (pair label or None-group, case, correct tok, p_corr, wrong tok, p_wrong, verdict, model top-1, p_top)
 ("closure capture — FAIL: same answer to both",
  [("closure-late-binding",    "'2'", 0.88, "'0'", 79.30, "MISS", "'0'", 79.30),
   ("closure-default-arg-fix", "'0'", 66.80, "'2'", 1.39, "ok",  "'0'", 66.80)]),
 ("default argument — FAIL: same answer to both",
  [("mutable-default",     "'1'", 89.06, "'2'", 10.64, "ok",   "'1'", 89.06),
   ("mutable-default-fix", "'2'", 6.74, "'1'", 93.36, "MISS", "'1'", 93.36)]),
 ("does the loop var leak — FAIL: exactly inverted",
  [("for-var-leaks",        "'2'", 1.56, "'Traceback'", 35.55, "MISS", "'Traceback'", 35.55),
   ("comprehension-no-leak","'Traceback'", 5.15, "'2'", 33.59, "MISS", "'2'", 33.59)]),
 ("class-body comprehension — FAIL: illegal one at noise",
  [("class-body-scope", "'Traceback'", 0.54, "'>'", 0.07, "noise", "'>>>'", 70.70),
   ("class-body-ok",    "'2'", 93.75, "'Traceback'", 0.00, "ok",   "'2'", 93.75)]),
 ("unpaired",
  [("tuple-augmented-assign", "','", 32.81, "']'", 0.60, "ok",   "'],'", 37.11),
   ("chained-comparison",     "'False'", 5.30, "'True'", 11.23, "MISS", "'>>>'", 64.45)]),
]

W = 760
LAB = 216
X0, X1 = LAB, 656
BAR, INGAP, CGAP, PGAP, HDR = 9, 3, 14, 26, 17
TOP = 42
def x(v): return X0 + v/100*(X1-X0)

out, y = [], TOP
rows = []
for label, cases in CASES:
    rows.append(("hdr", y, label)); y += HDR
    for c in cases:
        rows.append(("case", y, c)); y += BAR*2 + INGAP + CGAP
    y += PGAP - CGAP
H = y + 30

for t in (0, 25, 50, 75, 100):
    out.append(f'<line x1="{x(t):.1f}" y1="{TOP-16}" x2="{x(t):.1f}" y2="{H-28:.0f}" stroke="var(--viz-grid)" stroke-width="1"/>')
    out.append(f'<text x="{x(t):.1f}" y="{TOP-22}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-3)">{t}%</text>')
out.append(f'<line x1="{x(0):.1f}" y1="{TOP-16}" x2="{x(0):.1f}" y2="{H-28:.0f}" stroke="var(--viz-axis)" stroke-width="1.5"/>')
# noise floor marker at 2%
out.append(f'<line x1="{x(2):.1f}" y1="{TOP-16}" x2="{x(2):.1f}" y2="{H-28:.0f}" stroke="var(--viz-ink-3)" stroke-width="1" stroke-dasharray="3 4" opacity=".6"/>')
out.append(f'<text x="{x(2)+4:.1f}" y="{H-32:.0f}" font-size="10" fill="var(--viz-ink-3)">2% noise floor</text>')

esc = lambda s: s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("'","&#8217;" ) if False else s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
for kind, yy, payload in rows:
    if kind == "hdr":
        lab = esc(payload)
        col = "var(--viz-ink-2)" if payload == "unpaired" else "var(--viz-ink-1)"
        out.append(f'<text x="0" y="{yy+11}" font-size="11" font-weight="600" fill="{col}">{lab}</text>')
        continue
    name, ct, pc, wt, pw, verdict, topt, ptop = payload
    out.append(f'<text x="{LAB-12}" y="{yy+BAR*2-2}" text-anchor="end" font-size="11" '
               f'font-family="ui-monospace, Menlo, monospace" fill="var(--viz-ink-1)">{name}</text>')
    out.append(f'<g><title>P({esc(ct)} — the correct continuation) = {pc:.2f}%</title>'
               f'<rect x="{x(0):.1f}" y="{yy}" width="{max(x(pc)-x(0),1.5):.1f}" height="{BAR}" rx="4" fill="var(--viz-s1)"/></g>')
    out.append(f'<g><title>P({esc(wt)} — the natural misreading) = {pw:.2f}%</title>'
               f'<rect x="{x(0):.1f}" y="{yy+BAR+INGAP}" width="{max(x(pw)-x(0),1.5):.1f}" height="{BAR}" rx="4" fill="var(--viz-s2)"/></g>')
    vmax = max(pc, pw)
    vcol = {"ok": "var(--viz-ink-2)", "MISS": "var(--viz-ink-1)", "noise": "var(--viz-ink-3)"}[verdict]
    wt_v = {"ok": "400", "MISS": "700", "noise": "400"}[verdict]
    out.append(f'<text x="{x(vmax)+8:.1f}" y="{yy+BAR*2-2}" font-size="11" font-weight="{wt_v}" fill="{vcol}">{verdict}</text>')

svg = "\n".join("\t\t\t\t\t\t\t" + s for s in out)
open("trapfig.svgfrag","w").write(f"{W} {H}\n"+svg)
print("trapfig", W, H)
