"""Inspect and edit existing .drawio diagrams with minimal, targeted changes.

Every operation keeps existing ids, styles and positions unless it is the thing
being changed. New shapes copy the diagram's own visual language and are
placed where they overlap nothing and do not land inside a boundary they don't
belong to. New connectors are routed around shapes.
"""
import heapq
import json
import re
from collections import Counter

from . import drawio as D
from .drawio import parse_style, format_style, shape_of, label_text
from .icons import Library
from .check import is_container, is_text
from .geometry import edge_path, seg_hits_rect
from .build import load_theme, wrap_words
from .layout import text_width

META_KEYS = ("archRole", "archType", "archTech", "archIcon", "archKind", "archFlow")


# ------------------------------------------------------------------ classification
def classify(page):
    """Role of every cell: component, container (real or visual), text, icon, legend, edge, edge-label, layer."""
    roles, visual = {}, set()
    layers = page.layer_ids()
    rects = {}
    for cid in page.by_id:
        if cid in layers:
            roles[cid] = "layer"
        elif page.is_edge(cid):
            roles[cid] = "edge"
    for cid in page.by_id:
        if cid in roles:
            continue
        parent = page.cell(cid).get("parent")
        if parent in page.by_id and page.is_edge(parent):
            roles[cid] = "edge-label"
            continue
        r = page.abs_rect(cid)
        if r is None:
            roles[cid] = "other"
            continue
        rects[cid] = r
        names, st = parse_style(page.style(cid))
        ar = st.get("archRole")
        if ar:
            roles[cid] = {"component": "component", "boundary": "container", "legend": "legend",
                          "legend-item": "legend", "icon": "icon", "title": "text", "subtitle": "text",
                          "layer-label": "text"}.get(ar, "other")
            continue
        text = label_text(page.value(cid))
        if is_container(page, cid):
            roles[cid] = "legend" if "legend" in text.lower()[:20] else "container"
        elif is_text(page, cid):
            roles[cid] = "text"
        elif shape_of(page.style(cid)) == "image" and not text:
            roles[cid] = "icon"
        else:
            roles[cid] = "component"
    # hand-drawn legends: a "Legend"/"Key" text cell plus small swatches beside or below it
    for t, role in list(roles.items()):
        if role == "text" and re.fullmatch(r"(?i)\s*(legend|key)\s*:?\s*", label_text(page.value(t)) or ""):
            roles[t] = "legend"
            tx, ty, tw, th_ = rects[t]
            for c, r in list(roles.items()):
                if r in ("component", "text") and c in rects:
                    x, y, w, h = rects[c]
                    if h <= 40 and w <= 220 and ty - 10 <= y <= ty + 140 and x >= tx - 20:
                        roles[c] = "legend"
    # icons hiding as plain images on top of components, and visual boundaries drawn as plain rectangles
    comps = [c for c, r in roles.items() if r == "component"]
    for c in list(comps):
        r = rects[c]
        inside = [o for o in comps if o != c and _contains(r, rects[o])]
        names, st = parse_style(page.style(c))
        boxy = st.get("fillColor") in ("none", None) or st.get("dashed") == "1" or len(inside) >= 2
        if inside and boxy and shape_of(page.style(c)) in ("rect", "label", "swimlane"):
            roles[c] = "container"
            visual.add(c)
    return roles, visual, rects


def _contains(outer, inner, tol=1):
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1] and
            outer[0] + outer[2] + tol >= inner[0] + inner[2] and outer[1] + outer[3] + tol >= inner[1] + inner[3])


def _overlaps(a, b, margin=0):
    return (a[0] < b[0] + b[2] + margin and b[0] < a[0] + a[2] + margin and
            a[1] < b[1] + b[3] + margin and b[1] < a[1] + a[3] + margin)


def members_of(page, roles, visual, rects, cont):
    if cont in visual:
        return [c for c, r in roles.items() if r in ("component", "container") and c != cont and c in rects
                and _contains(rects[cont], rects[c])]
    return [c for c in page.descendants(cont) if roles.get(c) in ("component", "container")]


def icon_of(page, roles, rects, cid):
    """'image-node', a child/overlay icon id, or None."""
    if shape_of(page.style(cid)) == "image":
        return "image-node"
    for ch in page.children.get(cid, []):
        if roles.get(ch) == "icon":
            return ch
    r = rects.get(cid)
    if r:
        for o, role in roles.items():
            if role == "icon" and o in rects and _contains(r, rects[o]) and page.cell(o).get("parent") != cid:
                return o
    return None


def profile(page, roles, rects):
    comps = [c for c, r in roles.items() if r == "component" and c in rects]
    edges = [c for c, r in roles.items() if r == "edge"]
    clean = lambda st: format_style(*_strip(st, ("fillColor", "strokeColor", "dashed", "image", "imageBackground", "imageBorder") + META_KEYS))
    styles = Counter(clean(page.style(c)) for c in comps)
    sizes = Counter((round(rects[c][2]), round(rects[c][3])) for c in comps if shape_of(page.style(c)) != "image")
    ecl = Counter(format_style(*_strip(page.style(e), META_KEYS + ("exitX", "exitY", "exitDx", "exitDy", "entryX",
                                                                     "entryY", "entryDx", "entryDy", "dashed", "dashPattern",
                                                                     "startArrow", "startFill", "startSize")))
                  for e in edges if parse_style(page.style(e))[1].get("archRole") != "legend-item")
    icons = Counter("image-node" if icon_of(page, roles, rects, c) == "image-node" else
                    ("badge" if icon_of(page, roles, rects, c) else "none") for c in comps)
    if icons.get("image-node", 0) >= max(1, len(comps) // 2):
        conv = "icon-nodes"
    elif icons.get("badge", 0) >= 1:
        conv = "badges"
    else:
        conv = "none"
    fonts = Counter((parse_style(page.style(c))[1].get("fontFamily", "Helvetica"),
                     parse_style(page.style(c))[1].get("fontSize", "11")) for c in comps)
    fills = Counter(parse_style(page.style(c))[1].get("fillColor", "#ffffff") for c in comps)
    return {"component_style": styles.most_common(1)[0][0] if styles else None,
            "component_size": sizes.most_common(1)[0][0] if sizes else (190, 60),
            "edge_style": ecl.most_common(1)[0][0] if ecl else None,
            "icon_convention": conv, "font": fonts.most_common(1)[0][0] if fonts else ("Helvetica", "12"),
            "fills": fills.most_common(6)}


def _strip(style, keys):
    names, st = parse_style(style)
    for k in keys:
        st.pop(k, None)
    return names, st


# ------------------------------------------------------------------ inspect
def inspect(path, page_name=None, as_json=False):
    lib = Library()
    tree, pages = D.load(path)
    page = D.pick_page(pages, page_name)
    roles, visual, rects = classify(page)
    prof = profile(page, roles, rects)
    comps = [c for c, r in roles.items() if r == "component"]
    conts = [c for c, r in roles.items() if r == "container"]
    edges = [c for c, r in roles.items() if r == "edge" and parse_style(page.style(c))[1].get("archRole") != "legend-item"]
    data = {"page": page.name, "pages": [p.name for p in pages], "profile": prof, "containers": [], "components": [],
            "connectors": []}
    for c in conts:
        data["containers"].append({"id": c, "label": label_text(page.value(c)).replace("\n", " "),
                                   "kind": "visual box (plain rectangle; contents don't move with it)" if c in visual else "draw.io container",
                                   "rect": [round(v) for v in rects[c]],
                                   "members": members_of(page, roles, visual, rects, c)})
    for c in comps:
        st = parse_style(page.style(c))[1]
        text = label_text(page.value(c))
        inside = [k for k in conts if c in members_of(page, roles, visual, rects, k)]
        ic = icon_of(page, roles, rects, c)
        techs = [k for _, k in lib.detect_in_label(text)]
        data["components"].append({"id": c, "label": text.replace("\n", " | "), "rect": [round(v) for v in rects[c]],
                                   "in": inside, "icon": ic and ("image" if ic == "image-node" else st.get("archIcon") or "badge"),
                                   "tech": st.get("archTech") or None, "type": st.get("archType") or None,
                                   "tech_in_label": techs, "fill": st.get("fillColor")})
    for e in edges:
        cell = page.cell(e)
        st = parse_style(page.style(e))[1]
        data["connectors"].append({"id": e, "from": cell.get("source"), "to": cell.get("target"),
                                   "label": label_text(page.value(e)).replace("\n", " "),
                                   "style": "dashed" if st.get("dashed") == "1" else "solid",
                                   "waypoints": len(page.edge_points(e))})
    if as_json:
        return json.dumps(data, indent=1)
    out = [f"Page '{page.name}'" + (f" (pages: {', '.join(data['pages'])})" if len(pages) > 1 else ""),
           f"{len(comps)} components, {len(edges)} connectors, {len(conts)} containers "
           f"({sum(1 for c in conts if c not in visual)} draw.io containers, {sum(1 for c in conts if c in visual)} plain boxes)",
           f"Style: components mostly {prof['component_size'][0]}x{prof['component_size'][1]}, font {prof['font'][0]} {prof['font'][1]}, "
           f"icons: {prof['icon_convention']}, fills: " + ", ".join(f"{f} x{n}" for f, n in prof["fills"]),
           "", "Containers:"]
    for c in data["containers"]:
        out.append(f"  {c['id']}: '{c['label']}' [{c['kind']}] members: {', '.join(c['members']) or '-'}")
    out.append("\nComponents:")
    names = {c["id"]: c["label"].split(" | ")[0] for c in data["components"]}
    for c in data["components"]:
        extra = []
        if c["in"]:
            extra.append("in " + "/".join(c["in"]))
        extra.append(f"icon: {c['icon'] or 'none'}")
        if c["tech"]:
            extra.append(f"tech: {c['tech']}")
        elif c["tech_in_label"]:
            extra.append("label names: " + ", ".join(c["tech_in_label"]))
        out.append(f"  {c['id']}: '{c['label']}' at ({c['rect'][0]},{c['rect'][1]}) {c['rect'][2]}x{c['rect'][3]}; " + "; ".join(extra))
    out.append("\nConnectors:")
    for e in data["connectors"]:
        lab = f" '{e['label']}'" if e["label"] else ""
        out.append(f"  {e['id']}: {names.get(e['from'], e['from'])} -> {names.get(e['to'], e['to'])}{lab} [{e['style']}]")
    if visual:
        out.append("\nNote: some boundaries are plain rectangles, not draw.io containers. Patch operations treat "
                   "shapes drawn inside them as members, and will not place unrelated shapes inside them.")
    return "\n".join(out)


# ------------------------------------------------------------------ routing for edits
def route_edge(page, roles, rects, src, tgt, extra_obstacles=(), margin=12):
    sr, tr = rects[src], rects[tgt]
    skip = {src, tgt} | set(page.ancestors(src)) | set(page.ancestors(tgt)) | set(page.descendants(src)) | set(page.descendants(tgt))
    obst = [rects[c] for c, r in roles.items() if c in rects and c not in skip and r in ("component", "text", "legend")]
    for c, r in roles.items():
        if r == "container" and c in rects and label_text(page.value(c)):
            x, y, w, h = rects[c]
            obst.append((x, y, min(w, text_width(label_text(page.value(c)), 12, True) + 50), 30))
    obst += list(extra_obstacles)
    obst += [sr, tr]   # the path between the stubs must not cut through either end's own box
    infl = [(x - margin, y - margin, w + 2 * margin, h + 2 * margin) for x, y, w, h in obst]
    scx, scy = sr[0] + sr[2] / 2, sr[1] + sr[3] / 2
    tcx, tcy = tr[0] + tr[2] / 2, tr[1] + tr[3] / 2
    dx, dy = tcx - scx, tcy - scy
    if abs(dx) >= abs(dy):
        pairs = [("right", "left"), ("bottom", "top"), ("top", "bottom")] if dx > 0 else [("left", "right"), ("bottom", "top"), ("top", "bottom")]
    else:
        pairs = [("bottom", "top"), ("right", "left"), ("left", "right")] if dy > 0 else [("top", "bottom"), ("right", "left"), ("left", "right")]
    pairs += [("right", "right"), ("left", "left"), ("bottom", "bottom"), ("top", "top")]
    used = _used_fractions(page)
    best = None
    for s_side, t_side in pairs:
        fs, ft = _free_fraction(used.get((src, s_side), [])), _free_fraction(used.get((tgt, t_side), []))
        start, n1 = _port(sr, s_side, fs)
        end, n2 = _port(tr, t_side, ft)
        s1 = (start[0] + n1[0] * 18, start[1] + n1[1] * 18)
        e1 = (end[0] + n2[0] * 18, end[1] + n2[1] * 18)
        if any(_pt_in(s1, r) or _pt_in(e1, r) for r in infl):
            continue
        path = _astar(s1, e1, infl)
        if path is None:
            continue
        pts = _simplify([start, s1] + path + [e1, end])
        bends = len(pts) - 2
        length = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(pts, pts[1:]))
        cost = length + 40 * bends
        if best is None or cost < best[0]:
            best = (cost, s_side, fs, t_side, ft, pts)
    if best is None:
        return None
    _, s_side, fs, t_side, ft, pts = best
    return {"exit": _frac_xy(s_side, fs), "entry": _frac_xy(t_side, ft), "points": pts[1:-1], "path": pts}


def _used_fractions(page):
    used = {}
    for e in page.by_id:
        if not page.is_edge(e):
            continue
        st = parse_style(page.style(e))[1]
        c = page.cell(e)
        for end, key in (("exit", "source"), ("entry", "target")):
            if f"{end}X" in st and f"{end}Y" in st and c.get(key):
                fx, fy = float(st[f"{end}X"]), float(st[f"{end}Y"])
                side = "left" if fx <= 0 else "right" if fx >= 1 else "top" if fy <= 0 else "bottom" if fy >= 1 else None
                if side:
                    used.setdefault((c.get(key), side), []).append(fy if side in ("left", "right") else fx)
    return used


def _free_fraction(taken):
    for f in (0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6):
        if all(abs(f - t) > 0.08 for t in taken):
            return f
    return 0.5


def _port(r, side, f):
    x, y, w, h = r
    return {"right": ((x + w, y + f * h), (1, 0)), "left": ((x, y + f * h), (-1, 0)),
            "bottom": ((x + f * w, y + h), (0, 1)), "top": ((x + f * w, y), (0, -1))}[side]


def _frac_xy(side, f):
    return {"right": (1, f), "left": (0, f), "bottom": (f, 1), "top": (f, 0)}[side]


def _pt_in(p, r):
    return r[0] < p[0] < r[0] + r[2] and r[1] < p[1] < r[1] + r[3]


def _astar(a, b, rects, bend_cost=30):
    xs = sorted({a[0], b[0]} | {v for r in rects for v in (r[0], r[0] + r[2])})
    ys = sorted({a[1], b[1]} | {v for r in rects for v in (r[1], r[1] + r[3])})
    xs = sorted(set(xs + [(p + q) / 2 for p, q in zip(xs, xs[1:])] + [xs[0] - 40, xs[-1] + 40]))
    ys = sorted(set(ys + [(p + q) / 2 for p, q in zip(ys, ys[1:])] + [ys[0] - 40, ys[-1] + 40]))
    xi, yi = {v: i for i, v in enumerate(xs)}, {v: i for i, v in enumerate(ys)}
    blocked = lambda p: any(_pt_in(p, r) for r in rects)
    seg_ok = lambda p, q: not any(seg_hits_rect(p, q, r, inset=0.5) for r in rects)
    start, goal = (xi[a[0]], yi[a[1]]), (xi[b[0]], yi[b[1]])
    h = lambda n: abs(xs[n[0]] - xs[goal[0]]) + abs(ys[n[1]] - ys[goal[1]])
    pq = [(h(start), 0.0, start, None)]
    best = {(start, None): 0.0}
    prev = {}
    while pq:
        f, g, node, d = heapq.heappop(pq)
        if node == goal:
            path = [(xs[node[0]], ys[node[1]])]
            key = (node, d)
            while key in prev:
                key = prev[key]
                path.append((xs[key[0][0]], ys[key[0][1]]))
            return path[::-1]
        if g > best.get((node, d), float("inf")):
            continue
        for nd, (di, dj) in enumerate(((1, 0), (-1, 0), (0, 1), (0, -1))):
            ni, nj = node[0] + di, node[1] + dj
            if not (0 <= ni < len(xs) and 0 <= nj < len(ys)):
                continue
            p, q = (xs[node[0]], ys[node[1]]), (xs[ni], ys[nj])
            if blocked(q) or not seg_ok(p, q):
                continue
            ng = g + abs(p[0] - q[0]) + abs(p[1] - q[1]) + (bend_cost if d is not None and (nd // 2) != (d // 2) else 0)
            key = ((ni, nj), nd)
            if ng < best.get(key, float("inf")):
                best[key] = ng
                prev[key] = (node, d)
                heapq.heappush(pq, (ng + h((ni, nj)), ng, (ni, nj), nd))
    return None


def _simplify(pts):
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


# ------------------------------------------------------------------ patching
class Editor:
    def __init__(self, page, lib, theme):
        self.page, self.lib, self.th = page, lib, theme
        self.log = []
        self.refresh()

    def refresh(self):
        self.page.reindex()
        self.roles, self.visual, self.rects = classify(self.page)
        self.prof = profile(self.page, self.roles, self.rects)
        self.paths = {}
        for e, role in self.roles.items():
            if role == "edge" and parse_style(self.page.style(e))[1].get("archRole") != "legend-item":
                try:
                    pts, _ = edge_path(self.page, e)
                except Exception:
                    pts = []
                if pts:
                    self.paths[e] = pts

    # helpers -----------------------------------------------------------
    def _need(self, cid, *roles):
        if cid not in self.page.by_id:
            raise ValueError(f"no cell with id '{cid}'")
        if roles and self.roles.get(cid) not in roles:
            raise ValueError(f"'{cid}' is a {self.roles.get(cid)}, expected {' or '.join(roles)}")

    def _insert(self, cell, before=None):
        root = self.page.root
        if before is not None and before in list(root):
            root.insert(list(root).index(before), cell)
        else:
            root.append(cell)

    def _container_header(self, cid):
        return 34 if label_text(self.page.value(cid)) else 14

    def _solid(self):
        return [c for c, r in self.roles.items() if r in ("component", "text", "legend") and c in self.rects]

    def find_spot(self, w, h, anchor=None, boundary=None, side=None):
        self._avoid_edges = True
        spot = self._find_spot(w, h, anchor, boundary, side)
        if spot is None:
            self._avoid_edges = False
            spot = self._find_spot(w, h, anchor, boundary, side)
        return spot

    def _find_spot(self, w, h, anchor=None, boundary=None, side=None):
        page = self.page
        brect = self.rects.get(boundary) if boundary else None
        allowed = {boundary} | set(page.ancestors(boundary)) if boundary else set()
        if boundary in self.visual:
            allowed |= {c for c in self.roles if self.roles[c] == "container" and c in self.rects
                        and _contains(self.rects[c], brect)}
        if anchor is None and boundary:
            mem = [m for m in members_of(page, self.roles, self.visual, self.rects, boundary) if self.roles.get(m) == "component"]
            anchor = self.rects[mem[0]] if mem else (brect[0] + 20, brect[1] + 40, 1, 1)
        if anchor is None:
            xs = [r[0] + r[2] for r in self.rects.values()]
            ys = [r[1] for r in self.rects.values()]
            anchor = (max(xs) if xs else 40, min(ys) if ys else 40, 1, 1)
        gap = 70
        sides = [side] if side else []
        sides += [s for s in ("right", "below", "left", "above") if s not in sides]
        ax, ay, aw, ah = anchor
        for mult in (1, 1.6, 2.4, 3.4):
            for sd in sides:
                for shift in (0, 1, -1, 2, -2, 3, -3):
                    if sd in ("right", "left"):
                        x = ax + aw + gap * mult if sd == "right" else ax - gap * mult - w
                        y = ay + (ah - h) / 2 + shift * (h + 30)
                    else:
                        x = ax + (aw - w) / 2 + shift * (w + 40)
                        y = ay + ah + gap * mult if sd == "below" else ay - gap * mult - h
                    cand = (round(x / 10) * 10, round(y / 10) * 10, w, h)
                    if self._valid(cand, boundary, brect, allowed):
                        return cand
        return None

    def _valid(self, r, boundary, brect, allowed):
        if r[0] < 0 or r[1] < 0:
            return False
        for c in self._solid():
            if _overlaps(r, self.rects[c], 14):
                return False
        for c, role in self.roles.items():
            if role == "container" and c in self.rects and c not in allowed and _overlaps(r, self.rects[c], 10):
                return False
        if getattr(self, "_avoid_edges", False):
            grown = (r[0] - 8, r[1] - 8, r[2] + 16, r[3] + 16)
            for pts in self.paths.values():
                if any(seg_hits_rect(a, b, grown, inset=0) for a, b in zip(pts, pts[1:])):
                    return False
        if brect:
            hdr = self._container_header(boundary)
            if not (r[0] >= brect[0] + 14 and r[1] >= brect[1] + hdr and r[0] + r[2] <= brect[0] + brect[2] - 14
                    and r[1] + r[3] <= brect[1] + brect[3] - 14):
                return False
        return True

    def _grow_and_place(self, boundary, w, h, anchor=None):
        """Find room for a w x h shape by growing the boundary, shifting outside shapes if needed."""
        page = self.page
        allowed = lambda: {boundary} | set(page.ancestors(boundary))
        for attempt in ("down", "right", "down-shift", "right-shift"):
            br = self.rects[boundary]
            if attempt.startswith("down"):
                ax = anchor[0] + (anchor[2] - w) / 2 if anchor else br[0] + 20
                ax = min(max(ax, br[0] + 20), br[0] + br[2] - w - 20)
                cand = (round(ax / 10) * 10, round((br[1] + br[3] + 10) / 10) * 10, w, h)
            else:
                ay = anchor[1] + (anchor[3] - h) / 2 if anchor else br[1] + self._container_header(boundary)
                cand = (round((br[0] + br[2] + 10) / 10) * 10, round(max(ay, br[1] + self._container_header(boundary)) / 10) * 10, w, h)
            if attempt.endswith("shift"):
                outer = [a for a in [boundary] + page.ancestors(boundary) if self.roles.get(a) == "container"]
                top = outer[-1]
                tr = self.rects[top]
                if attempt.startswith("down"):
                    self.make_room(tr[1] + tr[3] + 1, h + 50, "y")
                else:
                    self.make_room(tr[0] + tr[2] + 1, w + 60, "x")
            if self.grow_to_fit(boundary, cand) and self._valid(cand, boundary, self.rects[boundary], allowed()):
                return cand
        return None

    def grow_to_fit(self, boundary, r):
        """Enlarge a boundary (and its ancestors) to fit rect r, if that captures no other shapes."""
        page = self.page
        chain = [boundary] + [a for a in page.ancestors(boundary) if self.roles.get(a) == "container"]
        target = r
        plan = []
        for b in chain:
            br = self.rects[b]
            hdr = self._container_header(b)
            nx0, ny0 = min(br[0], target[0] - 20), min(br[1], target[1] - hdr)
            nx1, ny1 = max(br[0] + br[2], target[0] + target[2] + 20), max(br[1] + br[3], target[1] + target[3] + 20)
            new = (nx0, ny0, nx1 - nx0, ny1 - ny0)
            if new == tuple(br):
                break
            members = set(members_of(page, self.roles, self.visual, self.rects, b)) | {b} | set(page.ancestors(b))
            for c in self._solid() + [k for k, v in self.roles.items() if v == "container" and k in self.rects]:
                if c in members or c in page.descendants(b):
                    continue
                if _overlaps(new, self.rects[c], 6) and not _contains(self.rects[c], new):
                    return False
            plan.append((b, new))
            target = new
        for b, new in plan:
            g = page.geometry(b)
            ox, oy = page.abs_origin(page.cell(b).get("parent"))
            g.set("x", D._num(new[0] - ox)); g.set("y", D._num(new[1] - oy))
            g.set("width", D._num(new[2])); g.set("height", D._num(new[3]))
        self.refresh()
        return True

    def make_room(self, line, amount, axis="y"):
        """Shift every top-level shape lying entirely beyond `line` by `amount`, keeping their arrangement."""
        page = self.page
        k, i = ("y", 1) if axis == "y" else ("x", 0)
        layer_children = [c for c in page.by_id if page.cell(c).get("parent") in page.layer_ids()]
        moved = []
        for c in layer_children:
            if page.is_vertex(c) and c in self.rects and self.rects[c][i] >= line - 0.5:
                g = page.geometry(c)
                g.set(k, D._num(float(g.get(k, 0)) + amount))
                moved.append(c)
        for e in layer_children:
            if page.is_edge(e):
                g = page.geometry(e)
                arr = g.find("Array") if g is not None else None
                if arr is not None:
                    for pt in arr.findall("mxPoint"):
                        if float(pt.get(k, 0)) >= line - 0.5:
                            pt.set(k, D._num(float(pt.get(k, 0)) + amount))
                for pt in (g.findall("mxPoint") if g is not None else []):
                    if float(pt.get(k, 0)) >= line - 0.5:
                        pt.set(k, D._num(float(pt.get(k, 0)) + amount))
        self.refresh()
        if moved:
            self.log.append(f"  made room: shifted {len(moved)} shape(s) {'down' if axis == 'y' else 'right'} by {amount:.0f}px")
        return moved

    def _parent_for(self, boundary):
        if boundary and boundary not in self.visual:
            return boundary
        if boundary and boundary in self.visual:
            reals = [a for a in self.page.ancestors(boundary) if a not in self.visual]
            return self.page.cell(boundary).get("parent") if not reals else reals[0]
        return self.page.default_layer()

    def _set_geometry(self, cid, abs_rect, parent):
        ox, oy = self.page.abs_origin(parent)
        g = self.page.geometry(cid)
        for k, v in zip(("x", "y", "width", "height"), (abs_rect[0] - ox, abs_rect[1] - oy, abs_rect[2], abs_rect[3])):
            g.set(k, D._num(v))

    def _badge(self, cid, key, replace=True):
        page = self.page
        for ch in list(page.children.get(cid, [])):
            if self.roles.get(ch) == "icon" and replace:
                page.root.remove(page.outer(ch))
        r = self.rects[cid]
        size = max(16, min(32, r[3] - 16))
        style = page.style(cid)
        names, st = parse_style(style)
        need = size + 18
        if float(st.get("spacingLeft", 0) or 0) < need:
            st["spacingLeft"] = str(int(need))
        st["collapsible"] = "0"
        page.set_style(cid, format_style(names, st))
        icon = D.make_vertex(page.new_id(f"{cid}-icon"), "",
                             f"shape=image;html=1;imageAspect=1;aspect=fixed;image={self.lib.data_uri(key)};connectable=0;archRole=icon;archIcon={key};",
                             cid, 10, (r[3] - size) / 2, size, size)
        self._insert(icon)

    # operations --------------------------------------------------------
    def add_component(self, op):
        page, th = self.page, self.th
        cid = op["id"]
        if cid in page.by_id:
            raise ValueError(f"id '{cid}' already exists")
        label = op["label"]
        boundary = op.get("boundary")
        if boundary:
            self._need(boundary, "container")
        like = op.get("like")
        if like:
            self._need(like, "component")
        tmpl = like or next((c for c, r in self.roles.items() if r == "component" and
                             format_style(*_strip(page.style(c), ("fillColor", "strokeColor", "dashed", "image",
                                                                  "imageBackground", "imageBorder") + META_KEYS)) == self.prof["component_style"]), None)
        r = self.lib.resolve(op.get("tech"), op.get("type"))
        conv = self.prof["icon_convention"]
        want_icon = op.get("icon", "auto")
        use_icon = (conv != "none") if want_icon == "auto" else bool(want_icon)
        use_icon = use_icon and r.icon_key is not None
        image_node = conv == "icon-nodes" and use_icon and (tmpl is None or shape_of(page.style(tmpl)) == "image")
        if tmpl:
            names, st = _strip(page.style(tmpl), META_KEYS)
            w, h = self.rects[tmpl][2], self.rects[tmpl][3]
            tmpl_value = page.value(tmpl)
        else:
            card = th["card"]
            names, st = parse_style(f"rounded=1;absoluteArcSize=1;arcSize={card['radius']};whiteSpace=wrap;html=1;fillColor={card['fill']};"
                                    f"strokeColor={card['stroke']};strokeWidth=1.2;fontFamily={th['font_family']};fontSize=12;"
                                    f"fontColor={th['text']};align=left;verticalAlign=middle;spacingLeft=14;spacingRight=8;")
            w, h = th["card"]["min_width"], th["card"]["height"]
            tmpl_value = "<b>x</b>"
        status = str(op.get("status") or "").lower()
        pal = th["status"].get(status)
        if status and not pal:
            raise ValueError(f"unknown status '{status}'")
        if pal and not image_node:
            st["fillColor"], st["strokeColor"] = pal["fill"], pal["stroke"]
            if pal.get("dashed"):
                st["dashed"] = "1"
            else:
                st.pop("dashed", None)
        if image_node:
            st["image"] = self.lib.data_uri(r.icon_key)
            st["archIcon"] = r.icon_key
            if pal:
                st["imageBackground"], st["imageBorder"] = pal["fill"], pal["stroke"]
        st["archRole"] = "component"
        if op.get("type"):
            st["archType"] = re.sub(r"[;=]", " ", str(op["type"]))
        if op.get("tech"):
            st["archTech"] = re.sub(r"[;=]", " ", str(op["tech"]))
        detail = op.get("detail") or (self.lib.label(r.tech_key) if r.tech_key and self.lib.label(r.tech_key).lower() not in label.lower() else "")
        if "<b>" in (tmpl_value or "") or "<br" in (tmpl_value or ""):
            m = re.search(r'<span style="[^"]*">', tmpl_value or "")
            span = m.group(0) if m else '<span style="font-size:10px;color:#6b7280;">'
            if image_node:
                value = "<br>".join(f"<b>{D.esc(x)}</b>" for x in wrap_words(label, 18))
            else:
                value = f"<b>{D.esc(label)}</b>"
            value += f"<br>{span}{D.esc(detail)}</span>" if detail else ""
        else:
            value = D.esc(label) + (f"<br>{D.esc(detail)}" if detail and st.get("html") == "1" else "")
        anchor = self.rects.get(op["near"]) if op.get("near") else None
        if op.get("near") and op["near"] not in self.rects:
            raise ValueError(f"near: no shape '{op['near']}'")
        spot = self.find_spot(w, h, anchor, boundary, op.get("side"))
        if spot is None and boundary:
            spot = self._grow_and_place(boundary, w, h, anchor)
        if spot is None:
            raise ValueError(f"could not find free space for '{cid}'" + (f" inside '{boundary}'" if boundary else "") +
                             "; move something or give a different 'near'/'side'")
        parent = self._parent_for(boundary)
        ox, oy = page.abs_origin(parent)
        cell = D.make_vertex(cid, value, format_style(names, st), parent, spot[0] - ox, spot[1] - oy, w, h)
        self._insert(cell)
        self.refresh()
        if use_icon and not image_node:
            self._badge(cid, r.icon_key)
            self.refresh()
        self._clear_through(cid)
        self.log.append(f"added component '{cid}' ({label}) at ({spot[0]:.0f},{spot[1]:.0f})" +
                        (f" in '{boundary}'" if boundary else "") +
                        (f", icon {r.icon_key} ({r.kind})" if use_icon else ", no icon"))
        if r.note:
            self.log.append(f"  icon note: {r.note}")

    def add_flow(self, op):
        page = self.page
        src, tgt = op["from"], op["to"]
        self._need(src, "component", "container")
        self._need(tgt, "component", "container")
        fid = op.get("id") or page.new_id(f"flow-{src}-{tgt}")
        if fid in page.by_id:
            raise ValueError(f"id '{fid}' already exists")
        route = route_edge(page, self.roles, self.rects, src, tgt)
        if route is None:
            raise ValueError(f"no clear path from '{src}' to '{tgt}'; make room first")
        base = self.prof["edge_style"] or (
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;endArrow=block;endFill=1;"
            f"strokeColor={self.th['edge']['color']};strokeWidth={self.th['edge']['width']};fontSize=11;labelBackgroundColor=#ffffff;")
        names, st = parse_style(base)
        st["edgeStyle"] = "orthogonalEdgeStyle"
        asyn = str(op.get("style", "sync")).lower() in ("async", "event", "events", "message")
        if asyn:
            st["dashed"], st["dashPattern"] = "1", "6 4"
        if op.get("bidirectional"):
            st["startArrow"], st["startFill"] = "block", "1"
        (ex, ey), (nx, ny) = route["exit"], route["entry"]
        st.update({"exitX": str(ex), "exitY": str(ey), "exitDx": "0", "exitDy": "0",
                   "entryX": str(nx), "entryY": str(ny), "entryDx": "0", "entryDy": "0",
                   "jumpStyle": "arc", "jumpSize": "8",          # U-bend hop where it crosses other lines
                   "archRole": "flow", "archFlow": "async" if asyn else "sync"})
        ca = [a for a in page.ancestors(src) if self.roles.get(a) == "container" and a not in self.visual]
        cb = [a for a in page.ancestors(tgt) if self.roles.get(a) == "container" and a not in self.visual]
        parent = next((a for a in ca if a in cb), page.default_layer())
        ox, oy = page.abs_origin(parent)
        pts = [(round(x - ox), round(y - oy)) for x, y in route["points"]]
        label = str(op.get("label") or "").strip()
        value = "<br>".join(D.esc(x) for x in wrap_words(label, 22)) if label else ""
        pos = self._label_pos(route["path"], label) if label else None
        self._insert(D.make_edge(fid, value, format_style(names, st), parent, src, tgt, pts, pos))
        self.refresh()
        self.log.append(f"added connector '{fid}': {src} -> {tgt}" + (f" '{label}'" if label else "") +
                        f" ({len(pts)} bends)")

    def _label_pos(self, pts, label):
        lines = wrap_words(label, 22)
        w, h = max(text_width(x, 11) for x in lines) + 10, 15 * len(lines) + 4
        total = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(pts, pts[1:])) or 1
        best, acc = None, 0.0
        obst = [self.rects[c] for c in self._solid()]
        from .geometry import point_at
        for e, epts in self.paths.items():
            lab = label_text(self.page.value(e)).replace("\n", " ")
            if not lab:
                continue
            g = self.page.geometry(e)
            px, py = point_at(epts, float(g.get("x", 0) or 0) if g is not None else 0.0)
            lw = text_width(lab, 11) + 10
            obst.append((px - lw / 2, py - 9, lw, 18))
        from .geometry import seg_hits_rect
        lines = [(a, b) for epts in self.paths.values() for a, b in zip(epts, epts[1:])]
        for a, b in zip(pts, pts[1:]):
            seg = abs(a[0] - b[0]) + abs(a[1] - b[1])
            for f in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88):
                d = acc + f * seg
                px, py = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                r = (px - w / 2, py - h / 2, w, h)
                score = sum(1000 for o in obst if _overlaps(r, o)) + abs(d / total - 0.5) * 50
                score += 1500 * sum(1 for c, dd in lines if seg_hits_rect(c, dd, r, inset=1.5))
                if best is None or score < best[0]:
                    best = (score, d)
            acc += seg
        return 2 * best[1] / total - 1 if best else None

    def remove(self, op):
        page = self.page
        cid = op["id"]
        self._need(cid)
        removed = []
        if self.roles.get(cid) == "container" and op.get("keep_children", True) and cid not in self.visual:
            new_parent = page.cell(cid).get("parent")
            ox, oy = page.abs_origin(new_parent)
            for ch in list(page.children.get(cid, [])):
                if self.roles.get(ch) == "icon":
                    continue
                if page.is_edge(ch):
                    cox, coy = page.abs_origin(cid)
                    g = page.geometry(ch)
                    arr = g.find("Array") if g is not None else None
                    if arr is not None:
                        for p in arr.findall("mxPoint"):
                            p.set("x", D._num(float(p.get("x", 0)) + cox - ox))
                            p.set("y", D._num(float(p.get("y", 0)) + coy - oy))
                    page.cell(ch).set("parent", new_parent)
                    continue
                r = page.abs_rect(ch)
                page.cell(ch).set("parent", new_parent)
                g = page.geometry(ch)
                g.set("x", D._num(r[0] - ox)); g.set("y", D._num(r[1] - oy))
            page.reindex()
            doomed = {cid} | {c for c in page.descendants(cid)}
        else:
            doomed = {cid} | set(page.descendants(cid))
        for e in list(page.by_id):
            if page.is_edge(e):
                c = page.cell(e)
                if c.get("source") in doomed or c.get("target") in doomed:
                    doomed.add(e)
                    doomed |= set(page.descendants(e))
        for d in doomed:
            if self.roles.get(d) in ("component", "container", "edge"):
                removed.append(f"{self.roles[d]} '{d}' ({label_text(page.value(d)).splitlines()[0] if label_text(page.value(d)) else ''})")
        for d in doomed:
            el = page.outer(d)
            if el is not None and el in list(page.root):
                page.root.remove(el)
        self.refresh()
        self.log.append(f"removed '{cid}'" + ("; also removed: " + "; ".join(r for r in removed if f"'{cid}'" not in r) if len(removed) > 1 else ""))

    def relabel(self, op):
        page = self.page
        cid = op["id"]
        self._need(cid)
        old = page.value(cid)
        st = parse_style(page.style(cid))[1]
        label = op["label"]
        if page.is_edge(cid):
            page.set_value(cid, "<br>".join(D.esc(x) for x in wrap_words(label, 22)))
        elif "<b>" in (old or ""):
            m = re.search(r"(<br>\s*<span[^>]*>)(.*?)(</span>)", old or "")
            detail = op.get("detail")
            if detail is None and m:
                tail = m.group(1) + m.group(2) + m.group(3)
            elif detail:
                span = m.group(1) if m else '<br><span style="font-size:10px;color:#6b7280;">'
                tail = f"{span}{D.esc(detail)}</span>"
            else:
                tail = ""
            head = ("<br>".join(f"<b>{D.esc(x)}</b>" for x in wrap_words(label, 18)) if shape_of(page.style(cid)) == "image"
                    else f"<b>{D.esc(label)}</b>")
            page.set_value(cid, head + tail)
        else:
            page.set_value(cid, D.esc(label) + (f"<br>{D.esc(op['detail'])}" if op.get("detail") and st.get("html") == "1" else ""))
        self.refresh()
        self.log.append(f"relabelled '{cid}': {label_text(old)!r} -> {label!r}")

    def set_icon(self, op):
        page = self.page
        cid = op["id"]
        self._need(cid, "component")
        key = op.get("icon")
        if key:
            if key not in self.lib.entries or not self.lib.available(key):
                raise ValueError(f"icon '{key}' is not in the local library")
            note = ""
        else:
            r = self.lib.resolve(op.get("tech"), op.get("type"))
            if not r.icon_key:
                raise ValueError(f"no icon for tech={op.get('tech')!r} type={op.get('type')!r}: {r.note}")
            key, note = r.icon_key, r.note
        names, st = parse_style(page.style(cid))
        if op.get("tech"):
            st["archTech"] = re.sub(r"[;=]", " ", str(op["tech"]))
        if op.get("type"):
            st["archType"] = re.sub(r"[;=]", " ", str(op["type"]))
        st.setdefault("archRole", "component")
        if shape_of(page.style(cid)) == "image":
            st["image"], st["archIcon"] = self.lib.data_uri(key), key
            page.set_style(cid, format_style(names, st))
        else:
            page.set_style(cid, format_style(names, st))
            self.refresh()
            existing = icon_of(page, self.roles, self.rects, cid)
            if existing and existing != "image-node" and page.cell(existing).get("parent") != cid:
                page.root.remove(page.outer(existing))
                self.refresh()
            self._badge(cid, key)
        self.refresh()
        self.log.append(f"icon of '{cid}' set to {key}" + (f" ({note})" if note else ""))

    def set_status(self, op):
        page = self.page
        cid = op["id"]
        self._need(cid, "component")
        pal = self.th["status"].get(str(op["status"]).lower())
        if not pal:
            raise ValueError(f"unknown status '{op['status']}' (use {', '.join(self.th['status'])})")
        names, st = parse_style(page.style(cid))
        if shape_of(page.style(cid)) == "image":
            st["imageBackground"], st["imageBorder"] = pal["fill"], pal["stroke"]
        else:
            st["fillColor"], st["strokeColor"] = pal["fill"], pal["stroke"]
            if pal.get("dashed"):
                st["dashed"] = "1"
            else:
                st.pop("dashed", None)
        page.set_style(cid, format_style(names, st))
        self.refresh()
        self.log.append(f"status of '{cid}' set to {op['status']}")

    def move_into(self, op):
        page = self.page
        cid, boundary = op["id"], op["boundary"]
        self._need(cid, "component")
        self._need(boundary, "container")
        w, h = self.rects[cid][2], self.rects[cid][3]
        old_parent = page.cell(cid).get("parent")
        saved = self.rects.pop(cid)
        role = self.roles.pop(cid)
        try:
            spot = self.find_spot(w, h, None, boundary, op.get("side"))
        finally:
            self.rects[cid], self.roles[cid] = saved, role
        if spot is None:
            self.rects.pop(cid)
            role = self.roles.pop(cid)
            try:
                spot = self._grow_and_place(boundary, w, h)
            finally:
                self.rects[cid], self.roles[cid] = page.abs_rect(cid), role
        if spot is None:
            raise ValueError(f"no room for '{cid}' inside '{boundary}'")
        parent = self._parent_for(boundary)
        page.cell(cid).set("parent", parent)
        self._set_geometry(cid, spot, parent)
        self.refresh()
        self.log.append(f"moved '{cid}' into '{boundary}' at ({spot[0]:.0f},{spot[1]:.0f})")
        self._reroute_connected(cid)
        self._clear_through(cid)

    def _clear_through(self, cid):
        """Reroute connectors that pass through or along shape cid, and move labels it covers."""
        from .geometry import point_at
        r = self.rects[cid]
        grown = (r[0] - 6, r[1] - 6, r[2] + 12, r[3] + 12)
        for e, pts in list(self.paths.items()):
            c = self.page.cell(e)
            if cid in (c.get("source"), c.get("target")):
                continue
            if any(seg_hits_rect(a, b, grown, inset=0) for a, b in zip(pts, pts[1:])):
                self.reroute({"id": e}, quiet=True)
                self.log.append(f"  rerouted '{e}' around '{cid}'")
                continue
            label = label_text(self.page.value(e)).replace("\n", " ")
            if label:
                g = self.page.geometry(e)
                pos = float(g.get("x", 0) or 0) if g is not None else 0.0
                px, py = point_at(pts, pos)
                w = text_width(label, 11) + 10
                if _overlaps((px - w / 2, py - 9, w, 18), r, 2):
                    new = self._label_pos(pts, label)
                    if new is not None:
                        if abs(round(new, 3)) >= 0.001:
                            g.set("x", D._num(round(new, 3)))
                        elif "x" in g.attrib:
                            del g.attrib["x"]
                        self.log.append(f"  moved the label of '{e}' off '{cid}'")
        self.refresh()

    def _reroute_connected(self, cid):
        page = self.page
        for e in list(page.by_id):
            if page.is_edge(e) and cid in (page.cell(e).get("source"), page.cell(e).get("target")):
                self.reroute({"id": e}, quiet=True)

    def reroute(self, op, quiet=False):
        page = self.page
        e = op["id"]
        self._need(e, "edge")
        c = page.cell(e)
        route = route_edge(page, self.roles, self.rects, c.get("source"), c.get("target"))
        if route is None:
            self.log.append(f"could not reroute '{e}' (no clear path)")
            return
        names, st = parse_style(page.style(e))
        (ex, ey), (nx, ny) = route["exit"], route["entry"]
        st.update({"edgeStyle": "orthogonalEdgeStyle", "exitX": str(ex), "exitY": str(ey), "exitDx": "0", "exitDy": "0",
                   "entryX": str(nx), "entryY": str(ny), "entryDx": "0", "entryDy": "0"})
        page.set_style(e, format_style(names, st))
        parent = c.get("parent")
        ox, oy = page.abs_origin(parent)
        g = page.geometry(e)
        arr = g.find("Array")
        if arr is not None:
            g.remove(arr)
        if route["points"]:
            import xml.etree.ElementTree as ET
            arr = ET.SubElement(g, "Array", {"as": "points"})
            for x, y in route["points"]:
                ET.SubElement(arr, "mxPoint", {"x": D._num(round(x - ox)), "y": D._num(round(y - oy))})
        label = label_text(page.value(e)).replace("\n", " ")
        pos = self._label_pos(route["path"], label) if label else None
        if pos is not None and abs(round(pos, 3)) >= 0.001:
            g.set("x", D._num(round(pos, 3)))
        elif "x" in g.attrib:
            del g.attrib["x"]
        self.refresh()
        if not quiet:
            self.log.append(f"rerouted '{e}'")

    def update_flow(self, op):
        page = self.page
        e = op["id"]
        self._need(e, "edge")
        names, st = parse_style(page.style(e))
        if "style" in op:
            if str(op["style"]).lower() in ("async", "event", "events", "message"):
                st["dashed"], st["dashPattern"], st["archFlow"] = "1", "6 4", "async"
            else:
                st.pop("dashed", None); st.pop("dashPattern", None); st["archFlow"] = "sync"
        if "bidirectional" in op:
            if op["bidirectional"]:
                st["startArrow"], st["startFill"] = "block", "1"
            else:
                st["startArrow"] = "none"
        page.set_style(e, format_style(names, st))
        if "label" in op:
            page.set_value(e, "<br>".join(D.esc(x) for x in wrap_words(str(op["label"]), 22)))
        self.refresh()
        self.log.append(f"updated connector '{e}'")

    def add_boundary(self, op):
        page, th = self.page, self.th
        bid = op["id"]
        if bid in page.by_id:
            raise ValueError(f"id '{bid}' already exists")
        members = op["members"]
        for m in members:
            self._need(m, "component", "container")
        rs = [self.rects[m] for m in members]
        hdr = 34
        x0, y0 = min(r[0] for r in rs) - 20, min(r[1] for r in rs) - hdr - 6
        x1, y1 = max(r[0] + r[2] for r in rs) + 20, max(r[1] + r[3] for r in rs) + 20
        rect = (x0, y0, x1 - x0, y1 - y0)
        inside_all = set(members) | {d for m in members for d in page.descendants(m)}
        intruders = [c for c in self._solid() + [k for k, v in self.roles.items() if v == "container" and k in self.rects]
                     if c not in inside_all and c not in page.ancestors(members[0]) and _overlaps(rect, self.rects[c], 0)
                     and not _contains(self.rects[c], rect)]
        if intruders:
            raise ValueError(f"a boundary around {members} would also cover {intruders}; move them first")
        parents = [set([page.cell(m).get("parent")]) for m in members]
        common = set.intersection(*parents)
        parent = op.get("parent") or (common.pop() if len(common) == 1 else page.default_layer())
        kind = str(op.get("kind") or "generic").lower()
        bs = th["boundaries"].get(kind, th["boundaries"]["generic"])
        style = (f"rounded=1;absoluteArcSize=1;arcSize=14;whiteSpace=wrap;html=1;container=1;collapsible=0;recursiveResize=0;"
                 f"fillColor={bs['fill']};strokeColor={bs['stroke']};dashed={bs['dashed']};strokeWidth=1.2;verticalAlign=top;"
                 f"align=left;spacingLeft=14;spacingTop=6;fontFamily={th['font_family']};fontSize=12;fontStyle=1;fontColor=#374151;"
                 f"archRole=boundary;archKind={kind};")
        ox, oy = page.abs_origin(parent)
        cell = D.make_vertex(bid, D.esc(op["label"]), style, parent, x0 - ox, y0 - oy, rect[2], rect[3])
        first = min((list(page.root).index(page.outer(m)) for m in members))
        page.root.insert(first, cell)
        page.reindex()
        for m in members:
            r = self.rects[m]
            page.cell(m).set("parent", bid)
            g = page.geometry(m)
            g.set("x", D._num(r[0] - x0)); g.set("y", D._num(r[1] - y0))
        self.refresh()
        self.log.append(f"added boundary '{bid}' around {', '.join(members)}")


def ensure_legend(ed):
    """Add legend rows for change-status colours used in the diagram but not yet explained."""
    page, th = ed.page, ed.th
    pal = th["status"]
    used = []
    for c, role in ed.roles.items():
        if role != "component":
            continue
        st = parse_style(page.style(c))[1]
        if shape_of(page.style(c)) == "image":
            fill, stroke = st.get("imageBackground"), st.get("imageBorder")
        else:
            fill, stroke = st.get("fillColor"), st.get("strokeColor")
        for k, v in pal.items():
            if fill and stroke and fill.lower() == v["fill"].lower() and stroke.lower() == v["stroke"].lower() and k not in used:
                used.append(k)
    if not used or used == ["unchanged"]:
        return []
    legend = next((c for c, r in ed.roles.items() if parse_style(page.style(c))[1].get("archRole") == "legend"), None)
    hand_drawn = [c for c, r in ed.roles.items() if r == "legend" and c != legend and
                  parse_style(page.style(c))[1].get("archRole") not in ("legend", "legend-item")]
    if legend is None and hand_drawn:
        texts = " | ".join(label_text(page.value(c)).lower() for c in hand_drawn)
        missing = [pal[k]["label"] for k in used if pal[k]["label"].lower() not in texts]
        if missing:
            ed.log.append("legend: the diagram's own legend doesn't mention " + ", ".join(missing) + "; add it by hand")
        return []
    added = []
    font = f"fontFamily={th['font_family']};"
    if legend is None:
        xs = [r[0] for r in ed.rects.values()]
        ys = [r[1] + r[3] for r in ed.rects.values()]
        legend = "legend"
        cell = D.make_vertex(legend, "Legend",
                             f"rounded=1;absoluteArcSize=1;arcSize=10;whiteSpace=wrap;html=1;container=1;collapsible=0;recursiveResize=0;"
                             f"fillColor=#ffffff;strokeColor=#cbd5e1;verticalAlign=top;align=left;spacingLeft=12;spacingTop=4;"
                             f"{font}fontSize=11;fontStyle=1;fontColor=#374151;archRole=legend;",
                             page.default_layer(), min(xs), max(ys) + 30, 210, 38)
        ed._insert(cell)
        ed.refresh()
    have = set(page.children.get(legend, []))
    g = page.geometry(legend)
    h = float(g.get("height"))
    for k in used:
        if f"legend-{k}" in have or f"legend-{k}" in page.by_id:
            continue
        st = pal[k]
        y = h - 8
        ed._insert(D.make_vertex(f"legend-{k}", "",
                                 f"rounded=1;absoluteArcSize=1;arcSize=6;html=1;fillColor={st['fill']};strokeColor={st['stroke']};"
                                 f"{'dashed=1;' if st.get('dashed') else ''}archRole=legend-item;", legend, 12, y + 2, 34, 14))
        ed._insert(D.make_vertex(f"legend-{k}-text", D.esc(st["label"]),
                                 f"text;html=1;align=left;verticalAlign=middle;{font}fontSize=11;fontColor={th['text']};archRole=legend-item;",
                                 legend, 56, y, 140, 18))
        h += 22
        added.append(st["label"])
    if added:
        g.set("height", D._num(h))
        ed.refresh()
        ed.log.append("legend now explains: " + ", ".join(added))
    return added


OPS = {"add_component": "add_component", "add_flow": "add_flow", "remove": "remove", "relabel": "relabel",
       "set_icon": "set_icon", "set_status": "set_status", "move_into": "move_into", "reroute": "reroute",
       "update_flow": "update_flow", "add_boundary": "add_boundary"}


def patch(path, ops, out, page_name=None):
    lib, th = Library(), load_theme()
    tree, pages = D.load(path)
    page = D.pick_page(pages, page_name or (ops.get("page") if isinstance(ops, dict) else None))
    oplist = ops["ops"] if isinstance(ops, dict) else ops
    ed = Editor(page, lib, th)
    for i, op in enumerate(oplist, 1):
        name = op.get("op")
        if name not in OPS:
            print(f"op #{i}: unknown op '{name}' (known: {', '.join(OPS)})")
            return 2
        try:
            getattr(ed, OPS[name])(op)
        except (ValueError, KeyError) as exc:
            print(f"op #{i} ({name}) failed: {exc}\nNothing was written.")
            return 2
    ensure_legend(ed)
    D.save(tree, out)
    print(f"Wrote {out}")
    for line in ed.log:
        print("  " + line)
    return 0


def apply_icons(path, out, page_name=None, mapping=None, dry_run=False, refresh=False):
    lib, th = Library(), load_theme()
    tree, pages = D.load(path)
    page = D.pick_page(pages, page_name)
    ed = Editor(page, lib, th)
    mapping = mapping or {}
    rows, changed = [], 0
    for cid, role in list(ed.roles.items()):
        if role != "component":
            continue
        st = parse_style(page.style(cid))[1]
        name = label_text(page.value(cid)).split("\n")[0]
        has = icon_of(page, ed.roles, ed.rects, cid)
        if has and not refresh and cid not in mapping:
            rows.append((cid, name, "-", "kept existing icon"))
            continue
        tech, ctype = mapping.get(cid) or st.get("archTech") or None, st.get("archType") or None
        if not tech:
            found = lib.detect_in_label(label_text(page.value(cid)))
            keys = list(dict.fromkeys(k for _, k in found))
            if len(keys) > 1:
                rows.append((cid, name, "?", f"ambiguous: label names {', '.join(keys)}; use --map {cid}=TECH"))
                continue
            if keys:
                tech = keys[0]
        if not tech and not ctype:
            rows.append((cid, name, "-", "no technology named; skipped"))
            continue
        r = lib.resolve(tech, ctype)
        if not r.icon_key:
            rows.append((cid, name, "-", r.note or "no icon"))
            continue
        if has == "image-node" or (has and parse_style(page.style(has) if has in page.by_id else "")[1].get("archIcon") == r.icon_key):
            if has == "image-node" and refresh and parse_style(page.style(cid))[1].get("archIcon") != r.icon_key and not dry_run:
                ed.set_icon({"id": cid, "icon": r.icon_key})
                changed += 1
                rows.append((cid, name, r.icon_key, f"{r.kind}; replaced"))
            else:
                rows.append((cid, name, r.icon_key, "already shown"))
            continue
        if not dry_run:
            ed.set_icon({"id": cid, "icon": r.icon_key, "tech": tech, "type": ctype})
            changed += 1
        rows.append((cid, name, r.icon_key, r.kind + (f" ({r.note})" if r.note else "")))
    w = max([len(r[1]) for r in rows] + [10])
    for cid, name, key, how in rows:
        print(f"  {name[:w]:<{w}}  {key:<26} {how}")
    if dry_run:
        print("(dry run: nothing written)")
        return 0
    D.save(tree, out)
    print(f"Wrote {out}: icons added or replaced on {changed} component(s)")
    return 0
