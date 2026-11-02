"""Edge path approximation matching draw.io's orthogonal routing closely enough for checks and previews."""
from .drawio import parse_style


def _side_point(rect, fx, fy):
    x, y, w, h = rect
    return x + fx * w, y + fy * h


def _side_of(fx, fy):
    if fx <= 0.001:
        return "left"
    if fx >= 0.999:
        return "right"
    if fy <= 0.001:
        return "top"
    if fy >= 0.999:
        return "bottom"
    return None


def _center(r):
    return r[0] + r[2] / 2, r[1] + r[3] / 2


def _auto_side(rect, toward):
    cx, cy = _center(rect)
    dx, dy = toward[0] - cx, toward[1] - cy
    if abs(dx) / max(rect[2], 1) >= abs(dy) / max(rect[3], 1):
        return ("right", (1, 0.5)) if dx >= 0 else ("left", (0, 0.5))
    return ("bottom", (0.5, 1)) if dy >= 0 else ("top", (0.5, 0))


def edge_path(page, eid):
    """Approximate absolute polyline of an edge. Returns (points, info)."""
    cell = page.cell(eid)
    _, st = parse_style(cell.get("style", ""))
    parent = cell.get("parent")
    ox, oy = page.abs_origin(parent)
    hints = [(x + ox, y + oy) for x, y in page.edge_points(eid)]
    g = page.geometry(eid)
    spt = tpt = None
    if g is not None:
        for p in g.findall("mxPoint"):
            if p.get("as") == "sourcePoint":
                spt = (float(p.get("x", 0)) + ox, float(p.get("y", 0)) + oy)
            elif p.get("as") == "targetPoint":
                tpt = (float(p.get("x", 0)) + ox, float(p.get("y", 0)) + oy)
    src, tgt = cell.get("source"), cell.get("target")
    srect = page.abs_rect(src) if src in page.by_id else None
    trect = page.abs_rect(tgt) if tgt in page.by_id else None
    if srect is None and spt is None or trect is None and tpt is None:
        return [], {"floating": True}
    s_center = _center(srect) if srect else spt
    t_center = _center(trect) if trect else tpt
    first_target = hints[0] if hints else t_center
    last_source = hints[-1] if hints else s_center
    if srect:
        if "exitX" in st and "exitY" in st:
            fx, fy = float(st["exitX"]), float(st["exitY"])
            start, s_side = _side_point(srect, fx, fy), _side_of(fx, fy)
            start = (start[0] + float(st.get("exitDx", 0) or 0), start[1] + float(st.get("exitDy", 0) or 0))
        else:
            s_side, f = _auto_side(srect, first_target)
            start = _side_point(srect, *f)
    else:
        start, s_side = spt, None
    if trect:
        if "entryX" in st and "entryY" in st:
            fx, fy = float(st["entryX"]), float(st["entryY"])
            end, t_side = _side_point(trect, fx, fy), _side_of(fx, fy)
            end = (end[0] + float(st.get("entryDx", 0) or 0), end[1] + float(st.get("entryDy", 0) or 0))
        else:
            t_side, f = _auto_side(trect, last_source)
            end = _side_point(trect, *f)
    else:
        end, t_side = tpt, None
    es = st.get("edgeStyle", "")
    orth = es in ("orthogonalEdgeStyle", "elbowEdgeStyle", "segmentEdgeStyle", "entityRelationEdgeStyle") or st.get("orthogonal") == "1"
    if not orth:
        return [start] + hints + [end], {"orthogonal": False}
    horizontal = s_side in ("left", "right") if s_side else (abs(first_target[0] - start[0]) >= abs(first_target[1] - start[1]))
    pts = [start]
    cur = start
    if hints:
        for h in hints:
            nxt = (h[0], cur[1]) if horizontal else (cur[0], h[1])
            pts.append(nxt)
            cur = nxt
            horizontal = not horizontal
        end_h = t_side in ("left", "right") if t_side else horizontal
        if end_h:
            # final segment must be horizontal: first reach the entry's y
            if not horizontal or abs(cur[1] - end[1]) > 0.5:
                pts.append((cur[0], end[1]))
        else:
            # final segment must be vertical: first reach the entry's x
            if horizontal or abs(cur[0] - end[0]) > 0.5:
                pts.append((end[0], cur[1]))
        pts.append(end)
    else:
        if horizontal and (t_side in ("left", "right") or t_side is None):
            mx = (start[0] + end[0]) / 2
            pts += [(mx, start[1]), (mx, end[1])]
        elif not horizontal and (t_side in ("top", "bottom") or t_side is None):
            my = (start[1] + end[1]) / 2
            pts += [(start[0], my), (end[0], my)]
        elif horizontal:
            pts.append((end[0], start[1]))
        else:
            pts.append((start[0], end[1]))
        pts.append(end)
    return _clean(pts), {"orthogonal": True}


def _clean(pts):
    out = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        if (abs(a[0] - b[0]) < 0.01 and abs(b[0] - c[0]) < 0.01) or (abs(a[1] - b[1]) < 0.01 and abs(b[1] - c[1]) < 0.01):
            out.pop(i)
        else:
            i += 1
    return out


def seg_hits_rect(a, b, r, inset=1.0):
    x, y, w, h = r
    x, y, w, h = x + inset, y + inset, w - 2 * inset, h - 2 * inset
    if w <= 0 or h <= 0:
        return False
    if abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01:
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        return x0 < x + w and x1 > x and y0 < y + h and y1 > y
    steps = 40
    for i in range(steps + 1):
        px = a[0] + (b[0] - a[0]) * i / steps
        py = a[1] + (b[1] - a[1]) * i / steps
        if x < px < x + w and y < py < y + h:
            return True
    return False


def point_at(pts, frac_pos):
    """Point at draw.io's relative label position x (-1 = source, 0 = middle, 1 = target)."""
    if len(pts) < 2:
        return pts[0] if pts else (0, 0)
    segs = [(a, b, abs(a[0] - b[0]) + abs(a[1] - b[1]) if (abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01)
             else ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5) for a, b in zip(pts, pts[1:])]
    total = sum(s[2] for s in segs) or 1
    d = (frac_pos + 1) * total / 2
    for a, b, ln in segs:
        if d <= ln or (a, b, ln) == segs[-1]:
            t = 0 if ln == 0 else min(1, d / ln)
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        d -= ln
    return pts[-1]
