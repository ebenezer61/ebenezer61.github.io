import math
DOWN_X   = [1.0,0.9,0.8,0.7,0.6,0.5,0.4,0.3,0.2,0.1,0.0]
DOWN_RES = [69.14,66.41,63.67,58.20,55.08,51.95,45.70,45.90,39.65,33.98,24.02]
DOWN_ANS = [25.39,27.54,30.08,35.35,37.89,40.43,45.70,45.90,50.78,55.86,65.23]
# freeze_attention=False: the perturbation also re-routes attention (intervention_results.txt, second sweep)
FREE_RES = [69.14,66.41,66.41,63.67,60.94,60.94,57.81,51.95,49.02,45.90,39.65]
FREE_ANS = [25.39,27.54,27.54,30.08,32.42,32.62,34.96,40.62,43.16,45.90,50.78]
UP_X     = [1,1.5,2,3,4,6,8,12,16,24,32]
UP_RES   = [69.14,68.75,66.80,66.41,66.02,65.23,71.88,78.52,82.42,85.16,85.94]
UP_ANS   = [25.39,25.39,27.73,27.54,27.54,27.15,20.61,12.01,7.67,4.22,2.60]

W, H = 760, 300
PT, PB = 34, 62
PANEL_W = 310
PX = [46, 46 + PANEL_W + 58]
def y(v): return PT + (100 - v) / 100 * (H - PT - PB)

def emit(px, xs, res, ans, logx, xticks, title, free=None):
    o = []
    lo, hi = (min(xs), max(xs))
    def X(v):
        if logx: return px + (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) * PANEL_W
        return px + (v - lo) / (hi - lo) * PANEL_W
    o.append(f'<text x="{px}" y="{PT-24}" font-size="11.5" fill="var(--viz-ink-1)" font-weight="600">{title}</text>')
    for g in (0, 25, 50, 75, 100):
        o.append(f'<line x1="{px}" y1="{y(g):.1f}" x2="{px+PANEL_W}" y2="{y(g):.1f}" stroke="var(--viz-grid)" stroke-width="1"/>')
        if px == PX[0]:
            o.append(f'<text x="{px-8}" y="{y(g)+4:.1f}" text-anchor="end" font-size="10.5" fill="var(--viz-ink-3)">{g}%</text>')
    for t in xticks:
        o.append(f'<text x="{X(t):.1f}" y="{H-PB+18:.0f}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-3)">{("%g"%t)}</text>')
        o.append(f'<line x1="{X(t):.1f}" y1="{y(0):.1f}" x2="{X(t):.1f}" y2="{y(0)+4:.1f}" stroke="var(--viz-axis)" stroke-width="1"/>')
    o.append(f'<line x1="{px}" y1="{y(0):.1f}" x2="{px+PANEL_W}" y2="{y(0):.1f}" stroke="var(--viz-axis)" stroke-width="1.5"/>')
    for series, col, nm in ((res, "var(--viz-s1)", "result"), (ans, "var(--viz-s2)", "ans")):
        d = " ".join(f'{"M" if i==0 else "L"}{X(v):.1f},{y(p):.1f}' for i,(v,p) in enumerate(zip(xs, series)))
        o.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        unit = "gain &#215;" if logx else "scaled to "
        for v,p in zip(xs, series):
            o.append(f'<g><title>{unit}{v:g} &#8594; P(&#8220; {nm}&#8221;) = {p:.2f}%</title>'
                     f'<circle cx="{X(v):.1f}" cy="{y(p):.1f}" r="4" fill="{col}" stroke="var(--viz-surface)" stroke-width="2"/></g>')
    if free:
        for series, col, nm in ((free[0], "var(--viz-s1)", "result"), (free[1], "var(--viz-s2)", "ans")):
            d = " ".join(f'{"M" if i==0 else "L"}{X(v):.1f},{y(p):.1f}' for i,(v,p) in enumerate(zip(xs, series)))
            o.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.6" stroke-dasharray="5 4" stroke-linejoin="round" stroke-linecap="round" opacity=".85"/>')
            for v,p in zip(xs, series):
                o.append(f'<g><title>scaled to {v:g}, attention free &#8594; P(&#8220; {nm}&#8221;) = {p:.2f}%</title>'
                         f'<circle cx="{X(v):.1f}" cy="{y(p):.1f}" r="3.2" fill="var(--viz-surface)" stroke="{col}" stroke-width="1.8"/></g>')
    return o, X

out = []
o1, X1 = emit(PX[0], DOWN_X, DOWN_RES, DOWN_ANS, False, [1.0,0.8,0.6,0.4,0.2,0.0],
              "Scale the &#8216;result&#8217; feature DOWN", free=(FREE_RES, FREE_ANS))
o2, X2 = emit(PX[1], UP_X, UP_RES, UP_ANS, True, [1,2,4,8,16,32],
              "Amplify the &#8216;ans&#8217; feature UP")
out += o1 + o2

# crossing band on the left panel (tied at 0.4 and 0.3)
cx0, cx1 = X1(0.4), X1(0.3)
out.append(f'<rect x="{min(cx0,cx1):.1f}" y="{PT-4}" width="{abs(cx1-cx0):.1f}" height="{y(0)-PT+4:.1f}" fill="var(--viz-ink-3)" opacity=".10"/>')
out.append(f'<text x="{(cx0+cx1)/2:.1f}" y="{PT-6}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-2)">tie &#8594; flip</text>')

# direct end labels
out.append(f'<text x="{X1(0.0)+6:.1f}" y="{y(24.02)+4:.1f}" font-size="11" fill="var(--viz-ink-1)">24.0%</text>')
out.append(f'<text x="{X1(0.0)+6:.1f}" y="{y(65.23)+4:.1f}" font-size="11" fill="var(--viz-ink-1)">65.2%</text>')
out.append(f'<text x="{X1(0.0)+6:.1f}" y="{y(39.65)+4:.1f}" font-size="10.5" fill="var(--viz-ink-2)">39.7% free</text>')
out.append(f'<text x="{X1(0.0)+6:.1f}" y="{y(50.78)+4:.1f}" font-size="10.5" fill="var(--viz-ink-2)">50.8% free</text>')
out.append(f'<text x="{X2(32)-4:.1f}" y="{y(85.94)-8:.1f}" text-anchor="end" font-size="11" fill="var(--viz-ink-1)">85.9%</text>')
out.append(f'<text x="{X2(32)-4:.1f}" y="{y(2.60)-8:.1f}" text-anchor="end" font-size="11" fill="var(--viz-ink-1)">2.6%</text>')

# axis captions
out.append(f'<text x="{PX[0]+PANEL_W/2:.0f}" y="{H-26}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-3)">gain on 18_4769 (the &#8216;result&#8217; feature)</text>')
out.append(f'<text x="{PX[1]+PANEL_W/2:.0f}" y="{H-26}" text-anchor="middle" font-size="10.5" fill="var(--viz-ink-3)">gain on 18_4732 (the &#8216;ans&#8217; feature)</text>')

svg = "\n".join("\t\t\t\t\t\t\t" + s for s in out)
open("fig2.svgfrag","w").write(f"{W} {H}\n"+svg)
print("fig2", W, H)
