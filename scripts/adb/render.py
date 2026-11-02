"""Approximate preview of a draw.io page as SVG/PNG, for checking layouts without draw.io.

Not pixel-identical to draw.io, but it draws the same geometry: shapes, nested
containers, embedded icons, labels, and connector paths.
"""
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .drawio import parse_style, shape_of
from .geometry import edge_path, point_at
from .layout import text_width


def _label_lines(value):
    """Split an HTML label into lines of (text, bold, size, color)."""
    if not value:
        return []
    parts = re.split(r"(?i)<br\s*/?>|</div>\s*<div[^>]*>|</p>\s*<p[^>]*>", value)
    out = []
    for part in parts:
        bold = bool(re.search(r"(?i)<b>|<strong>|font-weight:\s*bold", part))
        m = re.search(r"font-size:\s*([\d.]+)px", part)
        size = float(m.group(1)) if m else None
        m = re.search(r"(?:color:\s*|color=\")(#[0-9a-fA-F]{3,6})", part)
        color = m.group(1) if m else None
        text = html.unescape(re.sub(r"<[^>]+>", "", part)).replace("\xa0", " ").strip()
        if text:
            out.append((text, bold, size, color))
    return out


def _wrap(text, width, size, bold):
    if width <= 10 or text_width(text, size, bold) <= width:
        return [text]
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and text_width(trial, size, bold) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _color(v, default):
    if v in (None, "", "default"):
        return default
    return "none" if v == "none" else v


def render_svg(page, scale=2.0):
    verts, edges = [], []
    for cid, _, cell in page.items:
        if page.is_vertex(cid):
            r = page.abs_rect(cid)
            if r:
                verts.append((cid, r))
        elif page.is_edge(cid):
            edges.append(cid)
    if not verts:
        raise ValueError("page has no shapes")
    xs = [r[0] for _, r in verts] + [r[0] + r[2] for _, r in verts]
    ys = [r[1] for _, r in verts] + [r[1] + r[3] for _, r in verts]
    paths = {}
    for e in edges:
        pts, _ = edge_path(page, e)
        if pts:
            paths[e] = pts
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
    x0, y0, x1, y1 = min(xs) - 30, min(ys) - 30, max(xs) + 60, max(ys) + 50
    W, H = x1 - x0, y1 - y0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{W * scale:.0f}" height="{H * scale:.0f}" viewBox="{x0:.1f} {y0:.1f} {W:.1f} {H:.1f}">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>'
           '<marker id="arrd" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse" '
           'markerUnits="userSpaceOnUse"><path d="M0,0 L10,5 L0,10 z" fill="#475569"/></marker></defs>',
           f'<rect x="{x0}" y="{y0}" width="{W}" height="{H}" fill="#ffffff"/>']
    edge_set = set(edges)
    from .lines import has_jumps, jumps
    hops = jumps(paths, [e for e in edges if e in paths])
    for cid, _, cell in page.items:
        if cid in edge_set:
            if cid in paths:
                out.append(_svg_edge(page, cid, paths[cid], hops.get(cid) if has_jumps(page, cid) else None))
            continue
        if not page.is_vertex(cid):
            continue
        parent = cell.get("parent")
        if parent in edge_set:
            continue
        r = page.abs_rect(cid)
        if r:
            out.append(_svg_vertex(page, cid, r))
    out.append("</svg>")
    return "\n".join(out)


def _svg_vertex(page, cid, r):
    names, st = parse_style(page.style(cid))
    shp = shape_of(page.style(cid))
    x, y, w, h = r
    fill = _color(st.get("fillColor"), "#ffffff" if shp not in ("text", "image") else "none")
    stroke = _color(st.get("strokeColor"), "#000000" if shp not in ("text", "image") else "none")
    sw = float(st.get("strokeWidth", 1))
    dash = ' stroke-dasharray="6 4"' if st.get("dashed") == "1" else ""
    parts = []
    if shp == "image":
        bg, bd = st.get("imageBackground"), st.get("imageBorder")
        if bg or bd:
            parts.append(f'<rect x="{x - 3}" y="{y - 3}" width="{w + 6}" height="{h + 6}" rx="6" fill="{bg or "none"}" stroke="{bd or "none"}"/>')
        img = st.get("image", "")
        if img.startswith("data:") and ";base64," not in img and "," in img:
            head, payload = img.split(",", 1)
            img = f"{head};base64,{payload}"
        if img:
            parts.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet" xlink:href="{html.escape(img, quote=True)}"/>')
    elif shp in ("cylinder3", "cylinder", "mxgraph.flowchart.database"):
        ry = min(15.0, h * 0.15)
        parts.append(f'<path d="M{x},{y + ry} L{x},{y + h - ry} A{w / 2},{ry} 0 0 0 {x + w},{y + h - ry} L{x + w},{y + ry} '
                     f'A{w / 2},{ry} 0 0 0 {x},{y + ry} Z" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        parts.append(f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
    elif shp == "ellipse":
        parts.append(f'<ellipse cx="{x + w / 2}" cy="{y + h / 2}" rx="{w / 2}" ry="{h / 2}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
    elif shp == "umlActor":
        cx = x + w / 2
        parts.append(f'<circle cx="{cx}" cy="{y + h * 0.13}" r="{h * 0.12}" fill="{fill}" stroke="{stroke}"/>'
                     f'<path d="M{cx},{y + h * 0.25} V{y + h * 0.65} M{x},{y + h * 0.38} H{x + w} M{cx},{y + h * 0.65} L{x},{y + h} '
                     f'M{cx},{y + h * 0.65} L{x + w},{y + h}" stroke="{stroke}" fill="none"/>')
    elif shp == "rhombus":
        parts.append(f'<polygon points="{x + w / 2},{y} {x + w},{y + h / 2} {x + w / 2},{y + h} {x},{y + h / 2}" fill="{fill}" stroke="{stroke}"/>')
    elif shp != "text":
        rx = 0
        if st.get("rounded") == "1":
            rx = float(st.get("arcSize", 10)) if st.get("absoluteArcSize") == "1" else min(w, h) * float(st.get("arcSize", 10)) / 100
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
    parts.append(_svg_label(page.value(cid), st, x, y, w, h, shp))
    return "".join(parts)


def _svg_label(value, st, x, y, w, h, shp):
    lines = _label_lines(value)
    if not lines:
        return ""
    base_size = float(st.get("fontSize", 11))
    color = st.get("fontColor", "#000000")
    bold_all = int(st.get("fontStyle", 0) or 0) & 1
    align = st.get("align", "center")
    valign = st.get("verticalAlign", "middle")
    sl, sr = float(st.get("spacingLeft", 0)), float(st.get("spacingRight", 0))
    stp = float(st.get("spacingTop", 0))
    below = st.get("verticalLabelPosition") == "bottom"
    wrap = st.get("whiteSpace") == "wrap"
    avail = w - sl - sr - 8 if not below else max(w, 160)
    rows = []
    for text, bold, size, col in lines:
        size = size or base_size
        b = bold or bold_all
        for piece in (_wrap(text, avail, size, b) if wrap or below else [text]):
            rows.append((piece, b, size, col or color))
    total_h = sum(r[2] * 1.25 for r in rows)
    if below:
        ty = y + h + 4 + stp
    elif valign == "top":
        ty = y + 4 + stp
    elif valign == "bottom":
        ty = y + h - total_h - 4
    else:
        ty = y + (h - total_h) / 2
    if align == "left":
        tx, anchor = x + sl + 4, "start"
    elif align == "right":
        tx, anchor = x + w - sr - 4, "end"
    else:
        tx, anchor = x + sl / 2 + w / 2 - sr / 2, "middle"
    out = []
    for text, b, size, col in rows:
        ty += size * 1.05
        weight = ' font-weight="bold"' if b else ""
        out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-family="Helvetica, Liberation Sans, Arial, sans-serif" '
                   f'font-size="{size}" fill="{col}" text-anchor="{anchor}"{weight}>'
                   f'{html.escape(text)}</text>')
        ty += size * 0.2
    return "".join(out)


def _path_d(pts, hops, r):
    """Polyline as an SVG path; at each hop the line makes a small U-bend arc over the other line,
    like draw.io's jumpStyle=arc."""
    by_seg = {}
    for k, p in hops or ():
        by_seg.setdefault(k, []).append(p)
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        horiz = abs(a[1] - b[1]) < 0.5
        sign = 1 if ((b[0] - a[0]) if horiz else (b[1] - a[1])) > 0 else -1
        for x, y in sorted(by_seg.get(k, []), key=lambda p: ((p[0] - a[0]) if horiz else (p[1] - a[1])) * sign):
            sweep = 1 if sign > 0 else 0
            if horiz:
                d.append(f" L{x - sign * r:.1f},{y:.1f} A{r},{r} 0 0 {sweep} {x + sign * r:.1f},{y:.1f}")
            else:
                d.append(f" L{x:.1f},{y - sign * r:.1f} A{r},{r} 0 0 {sweep} {x:.1f},{y + sign * r:.1f}")
        d.append(f" L{b[0]:.1f},{b[1]:.1f}")
    return "".join(d)


def _svg_edge(page, cid, pts, hops=None):
    names, st = parse_style(page.style(cid))
    color = st.get("strokeColor", "#000000")
    sw = float(st.get("strokeWidth", 1))
    dash = ' stroke-dasharray="6 4"' if st.get("dashed") == "1" else ""
    end = st.get("endArrow", "classic") not in ("none", "")
    start = st.get("startArrow", "none") not in ("none", "")
    d = _path_d(pts, hops, float(st.get("jumpSize", 8)) / 2)
    marker = ' marker-end="url(#arrd)"' if end else ""
    marker += ' marker-start="url(#arrd)"' if start else ""
    out = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dash}{marker}/>']
    lines = _label_lines(page.value(cid))
    if lines:
        g = page.geometry(cid)
        pos = float(g.get("x", 0)) if g is not None and g.get("x") else 0.0
        px, py = point_at(pts, pos)
        size = float(st.get("fontSize", 11))
        widths = [text_width(t, size) for t, *_ in lines]
        bw, bh = max(widths) + 8, len(lines) * size * 1.25 + 4
        out.append(f'<rect x="{px - bw / 2:.1f}" y="{py - bh / 2:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="#ffffff"/>')
        ty = py - bh / 2 + 2
        for t, *_ in lines:
            ty += size * 1.05
            out.append(f'<text x="{px:.1f}" y="{ty:.1f}" font-family="Helvetica, Liberation Sans, Arial, sans-serif" font-size="{size}" '
                       f'fill="{st.get("fontColor", "#333333")}" text-anchor="middle">{html.escape(t)}</text>')
            ty += size * 0.2
    return "".join(out)


def find_browser():
    """A Chromium-based browser (Edge, Chrome, Chromium) for headless screenshots, or None."""
    import os
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    cands = [Path(b) / rel for b in pf if b for rel in ("Microsoft/Edge/Application/msedge.exe",
                                                         "Google/Chrome/Application/chrome.exe")]
    cands += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
              Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    return next((str(c) for c in cands if c.is_file()), None)


def _svg_size(svg_text):
    m = re.search(r'viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)', svg_text)
    return (float(m.group(1)), float(m.group(2))) if m else (1600.0, 900.0)


def browser_png(svg_text, out_png, canvas=None, scale=2.0):
    """Screenshot the SVG with a headless browser. With `canvas` (w, h) the diagram is fitted and
    centred on a white canvas of that size (e.g. 1920x1080 for a 16:9 slide); otherwise the PNG is
    the diagram's own size times `scale`. Returns True on success."""
    browser = find_browser()
    if not browser:
        return False
    w, h = _svg_size(svg_text)
    cw, ch = canvas or (int(w * scale), int(h * scale))
    out_png = Path(out_png).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        svg_path = Path(tmp) / "diagram.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        page = Path(tmp) / "page.html"
        page.write_text(f"<html><body style='margin:0;background:#fff;width:{cw}px;height:{ch}px;display:flex;"
                        f"align-items:center;justify-content:center'><img src='{svg_path.as_uri()}' "
                        f"style='max-width:{cw - (40 if canvas else 0)}px;max-height:{ch - (40 if canvas else 0)}px;"
                        f"width:100%;height:100%;object-fit:contain'></body></html>", encoding="utf-8")
        try:
            subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            "--allow-file-access-from-files", f"--user-data-dir={Path(tmp) / 'profile'}",
                            f"--window-size={cw},{ch}", f"--screenshot={out_png}", page.as_uri()],
                           capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return out_png.is_file() and out_png.stat().st_size > 0


def to_png(svg_text, out_png):
    """Rasterise with rsvg-convert, cairosvg, LibreOffice or a headless browser. Returns True on success."""
    out_png = Path(out_png)
    with tempfile.TemporaryDirectory() as tmp:
        svg_path = Path(tmp) / (out_png.stem + ".svg")
        svg_path.write_text(svg_text, encoding="utf-8")
        if shutil.which("rsvg-convert"):
            r = subprocess.run(["rsvg-convert", "-o", str(out_png), str(svg_path)], capture_output=True)
            if r.returncode == 0:
                return True
        try:
            import cairosvg
            cairosvg.svg2png(url=str(svg_path), write_to=str(out_png))
            return True
        except Exception:
            pass
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            r = subprocess.run([soffice, "--headless", "--convert-to", "png", "--outdir", tmp, str(svg_path)],
                               capture_output=True, timeout=180)
            produced = Path(tmp) / (out_png.stem + ".png")
            if r.returncode == 0 and produced.exists():
                shutil.move(str(produced), str(out_png))
                return True
    return browser_png(svg_text, out_png)


def find_drawio():
    """The draw.io desktop app, whose own export is exact (the preview renderer is an approximation)."""
    import os
    for name in ("drawio", "draw.io"):
        p = shutil.which(name)
        if p:
            return p
    cands = [Path(os.environ.get(k, "")) / "draw.io" / "draw.io.exe" for k in ("ProgramFiles", "LOCALAPPDATA")]
    cands += [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "draw.io" / "draw.io.exe",
              Path("/Applications/draw.io.app/Contents/MacOS/draw.io"), Path("/usr/bin/drawio")]
    return next((str(c) for c in cands if str(c).strip() and c.is_file()), None)


def export(page, page_index, src_file, out_path, canvas=None):
    """Export a page for use elsewhere. Uses the draw.io desktop app when installed (exact output),
    else the preview renderer plus a converter or headless browser. `canvas` (w, h) fits the
    diagram onto a fixed-size white image, e.g. (1920, 1080) for a 16:9 slide.
    Returns (path, how)."""
    out_path = Path(out_path)
    drawio = find_drawio()
    if drawio and not canvas:
        fmt = out_path.suffix.lower().lstrip(".") or "png"
        args = [drawio, "-x", "-f", fmt, "-p", str(page_index), "-o", str(out_path), str(src_file)]
        if fmt == "png":
            args[2:2] = ["--scale", "2", "-b", "10"]
        try:
            r = subprocess.run(args, capture_output=True, timeout=180)
            if r.returncode == 0 and out_path.is_file():
                return str(out_path), "draw.io desktop (exact)"
        except (OSError, subprocess.TimeoutExpired):
            pass
    svg = render_svg(page)
    if out_path.suffix.lower() == ".svg":
        out_path.write_text(svg, encoding="utf-8")
        return str(out_path), "preview renderer (close to draw.io)"
    if canvas and browser_png(svg, out_path, canvas=canvas):
        return str(out_path), f"preview renderer on a {canvas[0]}x{canvas[1]} canvas"
    if to_png(svg, out_path):
        return str(out_path), "preview renderer (close to draw.io)"
    return None, "no PNG converter, browser or draw.io desktop found; export SVG instead"


def preview(page, out_path):
    """Write a preview. .svg is always possible; .png needs rsvg-convert, cairosvg, LibreOffice or Edge/Chrome."""
    out_path = Path(out_path)
    svg = render_svg(page)
    if out_path.suffix.lower() == ".svg":
        out_path.write_text(svg, encoding="utf-8")
        return str(out_path)
    if to_png(svg, out_path):
        return str(out_path)
    fallback = out_path.with_suffix(".svg")
    fallback.write_text(svg, encoding="utf-8")
    return str(fallback)
