#!/usr/bin/env python3
"""lieflat_render.py — мост в скилл lieflat-charts (красивые графики).

ЛЮБАЯ нейросеть/агент в этом проекте рисует графики ТОЛЬКО через этот модуль
или напрямую через скилл lieflat-charts (~/.hermes/skills/lieflat-charts).
Стиль: Mono (бумага #F0EFEB + чернила #1C1C1A), карточки с круглыми углами,
Inter, «волосные» линии, один волосок = один день. Источник стиля —
mono-tokens.js скилла; геометрия — шаблон B3 hairline area / B2 hairline line
(templates/basics-gallery.html).

API:
    from tools.lieflat_render import render_equity_report
    render_equity_report(series, combined, title, out_png)
      series:   [{"name": "LKOH ge_xxx", "points": [(date, pnl_rub), ...]}, ...]
      combined: {"name": "PORTFOLIO", "points": [...]}  (или None)

Внутри: HTML (чистый SVG, детерминированный, офлайн) → headless chrome --screenshot.
Fallback: если chrome недоступен — matplotlib в ТОЙ ЖЕ mono-палитре (стиль не ломается).
Paper-only, брокерских вызовов нет.
"""
import json, shutil, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

# ── mono tokens (из ~/.hermes/skills/lieflat-charts/mono-tokens.js) ──
INK, PAPER, MUTED, FAINT, GRID = "#1C1C1A", "#F0EFEB", "#8F8E88", "#C6C5BF", "#DEDDD6"
LADDER = ["#1C1C1A", "#4A4944", "#6A6963", "#8F8E88", "#B0AFA9", "#C6C5BF", "#D8D7D1"]
FONT = ("'Inter',system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif")
CARD_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:__PAPER__;font-family:__FONT__;color:__INK__;padding:34px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1060px;margin:0 auto}
.pagehead h1{font-size:21px;letter-spacing:-.02em;font-weight:800}
.pagehead p{font-size:11.5px;color:__MUTED__;margin-top:4px;margin-bottom:22px}
.card{background:__PAPER__;border-radius:24px;padding:26px 28px 18px;margin-bottom:18px}
h2{font-weight:700;font-size:16px;letter-spacing:-.02em;margin-bottom:3px}
.sub{font-size:11px;color:__MUTED__;margin-bottom:12px}
.src{font-size:9px;color:__FAINT__;margin-top:10px;letter-spacing:.08em;font-weight:500;text-transform:uppercase}
svg{width:100%;display:block}
svg text{font-family:__FONT__}
"""


def _hairline_svg(points, w=960, h=300, color=INK, area=True, show_peaks=True):
    """Геометрия из шаблона B3 hairline area: один волосок = один день,
    сверху — тонкая линия контура, пик — точка с подписью."""
    if not points:
        return "<svg viewBox='0 0 960 300'></svg>"
    n = len(points)
    vals = [v for _, v in points]
    pad_l, pad_r, pad_t, pad_b = 34, 16, 34, 40
    iw, ih = w - pad_l - pad_r, h - pad_t - pad_b
    vmin, vmax = min(vals + [0.0]), max(vals + [0.0])
    if vmax == vmin:
        vmax = vmin + 1.0
    base = pad_t + ih * (vmax - 0.0) / (vmax - vmin)   # уровень нуля
    def x(i): return pad_l + iw * i / max(n - 1, 1)
    def y(v): return pad_t + ih * (vmax - v) / (vmax - vmin)
    parts = []
    # волоски дня от нулевой линии до значения
    step = max(1, n // 400)
    for i in range(0, n, step):
        parts.append(f"<line x1='{x(i):.1f}' y1='{base:.1f}' x2='{x(i):.1f}' y2='{y(vals[i]):.1f}' "
                     f"stroke='{MUTED}' stroke-width='0.6' opacity='0.55'/>")
    # нулевая линия + контур
    parts.append(f"<line x1='{pad_l}' y1='{base:.1f}' x2='{w-pad_r}' y2='{base:.1f}' stroke='{GRID}' stroke-width='0.9'/>")
    pts = " L ".join(f"{x(i):.1f} {y(v):.1f}" for i, v in enumerate(vals))
    parts.append(f"<path d='M{pts}' fill='none' stroke='{color}' stroke-width='1.5' stroke-linejoin='round'/>")
    # пик и дно — точки с подписями
    if show_peaks:
        for idx in (max(range(n), key=lambda i: vals[i]), min(range(n), key=lambda i: vals[i])):
            v = vals[idx]
            if abs(v) < 1:
                continue
            parts.append(f"<circle cx='{x(idx):.1f}' cy='{y(v):.1f}' r='4' fill='{color}'/>")
            dy = -10 if v > 0 else 18
            parts.append(f"<text x='{min(max(x(idx), pad_l+20), w-pad_r-20):.1f}' y='{y(v)+dy:.1f}' font-size='10' "
                         f"font-weight='800' fill='{color}' text-anchor='middle' "
                         f"style='paint-order:stroke;stroke:{PAPER};stroke-width:3px'>{v:+,.0f} ₽</text>")
    # месячные метки
    months, seen = [], set()
    for i, (d, _) in enumerate(points):
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            months.append((i, d.strftime("%b").upper()))
    for i, lab in months:
        parts.append(f"<text x='{x(i):.1f}' y='{h-pad_b+16:.1f}' font-size='7.5' font-weight='600' "
                     f"fill='{MUTED}' text-anchor='middle' letter-spacing='.1em'>{lab}</text>")
    parts.append(f"<text x='{w/2:.0f}' y='{h-6}' font-size='7' font-weight='600' fill='{FAINT}' "
                 f"text-anchor='middle' letter-spacing='.12em'>ONE HAIRLINE = ONE DAY · P&L RUB · PAPER ONLY</text>")
    return (f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='xMidYMid meet'>" + "".join(parts) + "</svg>")


def build_html(series, combined, title):
    css = CARD_CSS.replace("__PAPER__", PAPER).replace("__INK__", INK).replace("__MUTED__", MUTED) \
                  .replace("__FAINT__", FAINT).replace("__GRID__", GRID).replace("__FONT__", FONT)
    cards = []
    for i, s in enumerate(series):
        pts = s["points"]
        last = pts[-1][1] if pts else 0.0
        cards.append(f"""
<div class="card">
  <h2>{s['name']} — {last:+,.0f} ₽ за год</h2>
  <div class="sub">контрактов: {s.get('contracts', 1)} · equity дневная · капитал 20 000 ₽</div>
  {_hairline_svg(pts, color=LADDER[min(i, 3)])}
  <div class="src">HAIRLINE AREA · LIEFLAT MONO · {s.get('src', 'BACKTEST')}</div>
</div>""")
    if combined:
        pts = combined["points"]
        last = pts[-1][1] if pts else 0.0
        cards.insert(0, f"""
<div class="card">
  <h2>{combined['name']} — {last:+,.0f} ₽</h2>
  <div class="sub">суммарный P&L портфеля · {combined.get('sub', '')}</div>
  {_hairline_svg(pts, color=INK)}
  <div class="src">HAIRLINE AREA · LIEFLAT MONO · PORTFOLIO</div>
</div>""")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="wrap">
<div class="pagehead"><h1>{title}</h1>
<p>Стиль: lieflat-charts (Mono) · {datetime.now().strftime('%Y-%m-%d %H:%M')} · paper only, live orders: 0</p></div>
{''.join(cards)}
</div></body></html>"""


def render_equity_report(series, combined, title, out_png, width=1060):
    """Главная точка входа. Возвращает Path к PNG или None."""
    html = build_html(series, combined, title)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp_html = f.name
    ok = False
    if chrome:
        n_cards = len(series) + (1 if combined else 0)
        height = 200 + n_cards * 420
        try:
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                            f"--screenshot={out}", f"--window-size={width},{height}",
                            "--hide-scrollbars", "--virtual-time-budget=8000",
                            f"file://{tmp_html}"], capture_output=True, timeout=120)
            ok = out.exists() and out.stat().st_size > 10000
        except Exception as e:
            print(f"[lieflat] chrome failed: {e}", file=sys.stderr)
    if not ok:
        _fallback_matplotlib(series, combined, title, out)
    Path(tmp_html).unlink(missing_ok=True)
    return out if out.exists() else None


def _fallback_matplotlib(series, combined, title, out):
    """Fallback в ТОЙ ЖЕ mono-палитре скилла (стиль не ломается)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = len(series) + (1 if combined else 0)
    fig, axes = plt.subplots(n, 1, figsize=(11, 3.2 * n), sharex=True)
    axes = [axes] if n == 1 else axes
    fig.patch.set_facecolor(PAPER)
    rows = ([("PORTFOLIO " + combined["name"], combined, INK)] if combined else []) + \
           list(zip([s["name"] for s in series], series, LADDER))
    for ax, (name, s, color) in zip(axes, rows):
        ax.set_facecolor(PAPER)
        d = [p[0] for p in s["points"]]; v = [p[1] for p in s["points"]]
        ax.plot(d, v, lw=1.6, color=color)
        ax.axhline(0, color=GRID, lw=0.9)
        ax.set_title(name, fontsize=11, color=INK, fontweight="bold")
        ax.tick_params(colors=MUTED, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(GRID)
        ax.grid(alpha=0.25, color=GRID)
    fig.suptitle(title, fontsize=13, color=INK, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out, dpi=110, facecolor=PAPER)
    plt.close(fig)
