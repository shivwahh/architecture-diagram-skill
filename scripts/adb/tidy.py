"""Tidy connectors on an existing page without moving any shape or line:
- every connector gets line jumps (jumpStyle=arc), so a crossing shows as a small U-bend hop;
- a label that another line runs through (or that sits on a shape or another label) slides along
  its own connector to the clearest spot.
"""
from . import drawio as D
from .drawio import format_style, parse_style
from .geometry import edge_path, point_at, seg_hits_rect
from .lines import JUMP_STYLE, label_rect, through_labels


def _overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _connectors(page):
    out = []
    for cid, _, cell in page.items:
        if cell.get("edge") == "1" and cell.get("source") and cell.get("target"):
            if parse_style(cell.get("style", ""))[1].get("archRole") != "legend-item":
                out.append(cid)
    return out


def _shapes(page):
    out = []
    for cid, _, cell in page.items:
        if cell.get("vertex") != "1":
            continue
        st = parse_style(cell.get("style", ""))[1]
        if st.get("container") == "1" or st.get("archRole") in ("boundary", "legend"):
            continue
        r = page.abs_rect(cid)
        if r and r[2] > 0 and r[3] > 0:
            out.append(r)
    return out


def _set_pos(page, eid, pos):
    g = page.geometry(eid)
    g.set("relative", "1")
    if abs(pos) < 0.001:
        g.attrib.pop("x", None)
    else:
        g.set("x", D._num(round(pos, 3)))
    for p in list(g.findall("mxPoint")):
        if p.get("as") == "offset":
            g.remove(p)


def tidy_page(page, only=None, jumps=True):
    """Returns a dict of what changed. `only` limits the work to these connector ids
    (patch uses it for the connectors it added)."""
    edges = _connectors(page)
    todo = [e for e in edges if only is None or e in only]
    added = 0
    if jumps:
        for e in todo:
            names, st = parse_style(page.style(e))
            if st.get("jumpStyle", "none") in ("none", ""):
                st.update(JUMP_STYLE)
                page.set_style(e, format_style(names, st))
                added += 1
    paths = {e: edge_path(page, e)[0] for e in edges}
    paths = {e: p for e, p in paths.items() if len(p) >= 2}
    labels = {e: label_rect(page, e, p) for e, p in paths.items()}
    before = len(through_labels(paths, labels))
    shapes = _shapes(page)
    # labels to move: crossed by another line, or touched by a connector we were asked about
    bad = {f for e, f in through_labels(paths, labels)}
    if only is not None:
        bad = {f for f in bad if f in only} | {f for e, f in through_labels(paths, labels) if e in only}
    moved = []
    for e in sorted(bad, key=lambda x: -sum(1 for a, b in through_labels(paths, labels) if b == x)):
        if labels.get(e) is None:
            continue
        cur = labels[e]
        w, h = cur[2], cur[3]
        others = [(a, b) for f, p in paths.items() if f != e for a, b in zip(p, p[1:])]

        def score(r, pos):
            s = 3000 * sum(1 for a, b in others if seg_hits_rect(a, b, r, inset=1.5))
            s += 10 * sum(_overlap(r, o) for o in shapes)
            s += 6 * sum(_overlap(r, o) for f, o in labels.items() if f != e and o)
            return s + 20 * abs(pos)

        g = page.geometry(e)
        cur_pos = float(g.get("x", 0) or 0) if g is not None else 0.0
        best = (score(cur, cur_pos), cur_pos, cur)
        for i in range(-19, 20):
            pos = i / 20
            px, py = point_at(paths[e], pos)
            r = (px - w / 2, py - h / 2, w, h)
            sc = score(r, pos)
            if sc < best[0] - 1:
                best = (sc, pos, r)
        if best[1] != cur_pos:
            _set_pos(page, e, best[1])
            labels[e] = best[2]
            moved.append(e)
    after = len(through_labels(paths, labels))
    return {"jumps_added": added, "labels_moved": moved, "through_before": before, "through_after": after}
