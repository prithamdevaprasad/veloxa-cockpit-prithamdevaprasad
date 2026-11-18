"""Generates Veloxa's original illustration family: 14 synthetic-agent avatars and 7 department marks (SVG).
Faceless, flat, one palette. Output is committed under frontend/src/assets/art/."""
import sys
from pathlib import Path

out = Path(sys.argv[1])
(out / "agents").mkdir(parents=True, exist_ok=True)
(out / "departments").mkdir(parents=True, exist_ok=True)

SKIN = ["#F1D2B6", "#E0AF88", "#C38760", "#98633F", "#6B442A"]
HAIR = ["#2B2030", "#4A3326", "#7A5236", "#B8865A", "#D9D2C9", "#1E1A24"]
CLOTH = ["#9B7AE8", "#6F8FD6", "#5FA79A", "#C9925A", "#B07AB8", "#7C6CC4", "#8A9AAF"]
BG = ["#2E2540", "#33294A", "#2A2B45", "#30283F", "#352B43", "#2B2A3E", "#382C47"]


def hair_shape(style: str, c: str) -> tuple[str, str]:
    """(behind-head layer, over-head layer)"""
    if style == "short":
        return "", f'<path d="M19 27c0-9 6-14 13-14s13 5 13 13c-3-4-8-6-13-6s-10 3-13 7z" fill="{c}"/>'
    if style == "side":
        return "", f'<path d="M19 28c-1-10 6-15 14-15 7 0 12 5 12 12-6 0-12-2-16-6-2 4-6 7-10 9z" fill="{c}"/>'
    if style == "long":
        return (f'<path d="M17 28c0-10 7-16 15-16s15 6 15 16v18H17z" fill="{c}"/>',
                f'<path d="M19 26c1-8 6-12 13-12s12 4 13 12c-4-3-9-5-13-5s-9 2-13 5z" fill="{c}"/>')
    if style == "bun":
        return (f'<circle cx="32" cy="11" r="6" fill="{c}"/>',
                f'<path d="M19 27c0-9 6-14 13-14s13 5 13 14c-4-5-8-7-13-7s-9 2-13 7z" fill="{c}"/>')
    if style == "curly":
        dots = "".join(f'<circle cx="{x}" cy="{y}" r="5" fill="{c}"/>' for x, y in
                       [(21, 22), (25, 16), (32, 13), (39, 16), (43, 22), (20, 29), (44, 29)])
        return "", dots
    if style == "buzz":
        return "", f'<path d="M20 25c1-7 6-11 12-11s11 4 12 11c-3-2-7-3-12-3s-9 1-12 3z" fill="{c}" opacity=".85"/>'
    if style == "wrap":   # head covering
        return (f'<path d="M16 30c0-11 7-18 16-18s16 7 16 18c0 8-3 13-6 16H22c-3-3-6-8-6-16z" fill="{c}"/>', "")
    raise ValueError(style)


AGENTS = [  # (style, skin, hair, cloth, bg, glasses)
    ("short", 0, 1, 0, 0, False), ("long", 2, 0, 1, 1, False), ("curly", 4, 0, 2, 2, False),
    ("bun", 1, 3, 3, 3, True), ("side", 3, 0, 4, 4, False), ("wrap", 2, None, 5, 5, False),
    ("buzz", 4, 5, 6, 6, True), ("long", 0, 2, 2, 0, True), ("short", 3, 5, 1, 2, False),
    ("curly", 1, 4, 4, 1, False), ("side", 2, 1, 0, 3, True), ("bun", 4, 0, 6, 4, False),
    ("buzz", 1, 4, 3, 5, False), ("wrap", 0, None, 1, 6, True),
]
WRAP_COLORS = ["#5FA79A", "#C9925A"]

for i, (style, s, h, cl, bg, glasses) in enumerate(AGENTS, 1):
    hc = HAIR[h] if h is not None else WRAP_COLORS[i % 2]
    behind, over = hair_shape(style, hc)
    g = ('<g fill="none" stroke="#1E1A24" stroke-width="1.6"><circle cx="27" cy="29" r="3.6"/>'
         '<circle cx="37" cy="29" r="3.6"/><path d="M30.6 29h2.8"/></g>') if glasses else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img">'
           f'<circle cx="32" cy="32" r="32" fill="{BG[bg]}"/>'
           f'<clipPath id="c"><circle cx="32" cy="32" r="32"/></clipPath><g clip-path="url(#c)">'
           f'{behind}'
           f'<path d="M10 64c1-12 10-19 22-19s21 7 22 19z" fill="{CLOTH[cl]}"/>'
           f'<rect x="28" y="36" width="8" height="10" rx="3" fill="{SKIN[s]}"/>'
           f'<circle cx="32" cy="28" r="12" fill="{SKIN[s]}"/>'
           f'{over}{g}</g></svg>')
    (out / "agents" / f"agent-{i:02d}.svg").write_text(svg + "\n", encoding="utf-8")

# Department marks: line art on a plum tile, one lavender accent fill.
L, A = "#C1A7F5", "#9B7AE8"
STROKE = f'fill="none" stroke="{L}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
DEPTS = {
    "engineering": f'<rect x="14" y="17" width="36" height="28" rx="4" fill="{A}" fill-opacity=".22"/>'
                   f'<g {STROKE}><rect x="14" y="17" width="36" height="28" rx="4"/><path d="M14 24h36"/>'
                   f'<path d="M27 31l-4 4 4 4M37 31l4 4-4 4M33 30l-2 10"/></g>',
    "finance": f'<ellipse cx="32" cy="22" rx="12" ry="4.5" fill="{A}" fill-opacity=".3"/>'
               f'<g {STROKE}><ellipse cx="32" cy="22" rx="12" ry="4.5"/>'
               f'<path d="M20 22v7c0 2.5 5.4 4.5 12 4.5s12-2 12-4.5v-7"/>'
               f'<path d="M20 29v7c0 2.5 5.4 4.5 12 4.5s12-2 12-4.5v-7"/>'
               f'<path d="M20 36v6c0 2.5 5.4 4.5 12 4.5s12-2 12-4.5v-6"/></g>',
    "leadership": f'<path d="M18 46l10-18 6 9 4-5 8 14z" fill="{A}" fill-opacity=".25"/>'
                  f'<g {STROKE}><path d="M14 46h36"/><path d="M18 46l10-18 6 9 4-5 8 14"/><path d="M28 28V15"/>'
                  f'<path d="M28 15h9l-2 3 2 3h-9"/></g>',
    "marketing": f'<path d="M22 27l18-9v26l-18-9z" fill="{A}" fill-opacity=".25"/>'
                 f'<g {STROKE}><path d="M22 27l18-9v26l-18-9z"/><rect x="15" y="27" width="7" height="8" rx="2"/>'
                 f'<path d="M19 35l2 9h4l-2-9M45 26c2 1.5 2 8.5 0 10"/></g>',
    "operations": f'<circle cx="26" cy="34" r="5" fill="{A}" fill-opacity=".35"/>'
                  f'<g {STROKE}><circle cx="26" cy="34" r="9"/><circle cx="26" cy="34" r="3.2"/>'
                  f'<path d="M26 22v3M26 43v3M14 34h3M35 34h3M17.5 25.5l2.1 2.1M32.4 40.4l2.1 2.1'
                  f'M17.5 42.5l2.1-2.1M32.4 27.6l2.1-2.1"/>'
                  f'<circle cx="42" cy="22" r="5"/><path d="M42 14v2.5M42 27.5V30M34 22h2.5M47.5 22H50"/></g>',
    "people": f'<circle cx="26" cy="25" r="5" fill="{A}" fill-opacity=".35"/>'
              f'<g {STROKE}><circle cx="26" cy="25" r="5"/><path d="M16 45c0-6 4.5-10 10-10s10 4 10 10"/>'
              f'<circle cx="40" cy="27" r="4"/><path d="M38 35.5c5 0 9 3.5 9 9.5"/></g>',
    "sales": f'<path d="M16 18h32l-12 14v12l-8 4V32z" fill="{A}" fill-opacity=".22"/>'
             f'<g {STROKE}><path d="M16 18h32l-12 14v12l-8 4V32z"/><path d="M21 24h22"/></g>',
}
for name, body in DEPTS.items():
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img">'
           f'<rect width="64" height="64" rx="14" fill="#292235"/>{body}</svg>')
    (out / "departments" / f"{name}.svg").write_text(svg + "\n", encoding="utf-8")
print("ok", len(AGENTS), len(DEPTS))
