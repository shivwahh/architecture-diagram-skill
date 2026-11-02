"""Connector-on-connector problems: crossings, lines running on top of each other, and lines that
cut through another connector's label text.

A crossing is acceptable when it is drawn as a line jump (draw.io `jumpStyle=arc`, a small U-bend
hop). A line through another connector's label is not: the reader can't tell which line the text
belongs to. Shared by check, the preview renderer and the tidy-lines command.
"""
from .drawio import label_text, parse_style
from .geometry import point_at, seg_hits_rect
from .layout import text_width

JUMP_STYLE = {"jumpStyle": "arc", "jumpSize": "8"}


def _horizontal(a, b):
    return abs(a[1] - b[1]) < 0.5 and abs(a[0] - b[0]) > 0.5


def _vertical(a, b):
    return abs(a[0] - b[0]) < 0.5 and abs(a[1] - b[1]) > 0.5


def label_rect(page, eid, pts):
    """Absolute rectangle of a connector's label, or None when it has no text."""
    text = label_text(page.value(eid))
    if not text or len(pts) < 2:
        return None
    st = parse_style(page.style(eid))[1]
    size = float(st.get("fontSize", 11))
    g = page.geometry(eid)
    pos = float(g.get("x", 0) or 0) if g is not None else 0.0
    px, py = point_at(pts, pos)
    if g is not None:
        for p in g.findall("mxPoint"):
            if p.get("as") == "offset":
                px += float(p.get("x", 0) or 0)
                py += float(p.get("y", 0) or 0)
    rows = text.split("\n")
    w = max(text_width(r, size) for r in rows) + 6
    h = len(rows) * size * 1.25 + 2
    return (px - w / 2, py - h / 2, w, h)


def through_labels(paths, labels):
    """[(edge, other_edge)] where `edge` passes through the label of `other_edge`."""
    out = []
    for e, pts in paths.items():
        for f, r in labels.items():
            if f == e or r is None:
                continue
            if any(seg_hits_rect(a, b, r, inset=1.5) for a, b in zip(pts, pts[1:])):
                out.append((e, f))
    return out


def overlaps(paths, min_len=8.0, tol=3.5):
    """[(edge_a, edge_b, length)] where two connectors run along the same line (or within
    3 px of it, which looks the same at normal line widths)."""
    segs = [(e, a, b) for e, pts in paths.items() for a, b in zip(pts, pts[1:])]
    found = {}
    for i, (e1, a1, b1) in enumerate(segs):
        for e2, a2, b2 in segs[i + 1:]:
            if e1 == e2:
                continue
            if _horizontal(a1, b1) and _horizontal(a2, b2) and abs(a1[1] - a2[1]) <= tol:
                lo = max(min(a1[0], b1[0]), min(a2[0], b2[0]))
                hi = min(max(a1[0], b1[0]), max(a2[0], b2[0]))
            elif _vertical(a1, b1) and _vertical(a2, b2) and abs(a1[0] - a2[0]) <= tol:
                lo = max(min(a1[1], b1[1]), min(a2[1], b2[1]))
                hi = min(max(a1[1], b1[1]), max(a2[1], b2[1]))
            else:
                continue
            if hi - lo >= min_len:
                key = tuple(sorted((e1, e2)))
                found[key] = found.get(key, 0) + (hi - lo)
    return [(a, b, round(n)) for (a, b), n in found.items()]


def jumps(paths, order):
    """{edge: [(segment_index, point)]}: where each connector hops over connectors drawn before it
    (draw.io draws the jump on the connector that is later in the file)."""
    out = {e: [] for e in paths}
    earlier = []
    for e in order:
        pts = paths.get(e)
        if not pts:
            continue
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            for f, c, d in earlier:
                if _horizontal(a, b) and _vertical(c, d):
                    x, y = c[0], a[1]
                    if min(a[0], b[0]) + 9 < x < max(a[0], b[0]) - 9 and min(c[1], d[1]) + 0.5 < y < max(c[1], d[1]) - 0.5:
                        out[e].append((k, (x, y)))
                elif _vertical(a, b) and _horizontal(c, d):
                    x, y = a[0], c[1]
                    if min(a[1], b[1]) + 9 < y < max(a[1], b[1]) - 9 and min(c[0], d[0]) + 0.5 < x < max(c[0], d[0]) - 0.5:
                        out[e].append((k, (x, y)))
        earlier += [(e, a, b) for a, b in zip(pts, pts[1:])]
    return out


def has_jumps(page, eid):
    return parse_style(page.style(eid))[1].get("jumpStyle", "none") not in ("none", "")
