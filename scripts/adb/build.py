"""Build a native, editable .drawio file from an architecture spec (JSON)."""
import json
import re
from pathlib import Path

from . import drawio as D
from .icons import Library, normalize
from .layout import Layout, text_width

SKILL_ROOT = Path(__file__).resolve().parents[2]
ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
STYLES = ("auto", "card", "node", "none")


def load_theme():
    with open(SKILL_ROOT / "assets" / "theme.json", encoding="utf-8") as f:
        return json.load(f)


def theme_for(target, theme=None):
    """The theme with a target's (slide, document) sizes, card and spacing overrides applied."""
    th = json.loads(json.dumps(theme or load_theme()))
    cfg = th.get("targets", {}).get(target or "screen", {})
    for part in ("sizes", "card", "spacing"):
        th[part].update(cfg.get(part, {}))
    return th


class SpecError(Exception):
    pass


# ------------------------------------------------------------------ spec
def normalize_spec(spec):
    """Validate the spec and return a cleaned copy plus warnings. Raises SpecError on fatal problems."""
    errors, warnings = [], []
    direction = str(spec.get("direction", "LR")).upper()
    if direction not in ("LR", "TB"):
        errors.append("direction must be LR or TB")
    style = str(spec.get("icon_style", "auto")).lower()
    if style not in STYLES:
        errors.append(f"icon_style must be one of {STYLES}")
    target = str(spec.get("target", "screen")).lower()
    if target not in ("slide", "document", "screen"):
        errors.append("target must be slide, document or screen")
    comps = spec.get("components") or []
    bounds = spec.get("boundaries") or []
    flows = spec.get("flows") or []
    if not comps:
        errors.append("spec has no components")
    ids = {}
    for kind, items in (("component", comps), ("boundary", bounds)):
        for i, it in enumerate(items):
            cid = str(it.get("id", "")).strip()
            if not cid:
                errors.append(f"{kind} #{i + 1} has no id")
                continue
            if not ID_RE.match(cid):
                errors.append(f"{kind} id '{cid}' may only use letters, digits, '_', '-', '.'")
            if cid in ids:
                errors.append(f"id '{cid}' is used twice ({ids[cid]} and {kind})")
            ids[cid] = kind
            if not str(it.get("label", "")).strip():
                errors.append(f"{kind} '{cid}' has no label")
    bmap = {b["id"]: b for b in bounds if b.get("id")}
    for b in bounds:
        p = b.get("parent")
        if p and p not in bmap:
            errors.append(f"boundary '{b['id']}' has unknown parent '{p}'")
        seen, cur = set(), b.get("id")
        while cur in bmap:
            if cur in seen:
                errors.append(f"boundary '{b['id']}' is inside itself (parent loop)")
                break
            seen.add(cur)
            cur = bmap[cur].get("parent")
    cmap = {c["id"]: c for c in comps if c.get("id")}
    for c in comps:
        if c.get("boundary") and c["boundary"] not in bmap:
            errors.append(f"component '{c['id']}' is in unknown boundary '{c['boundary']}'")
    fids = set()
    for i, f in enumerate(flows):
        for end in ("from", "to"):
            if f.get(end) not in cmap:
                what = "a boundary" if f.get(end) in bmap else "unknown"
                errors.append(f"flow #{i + 1} {end} '{f.get(end)}' is {what}; flows must connect components")
        if f.get("from") == f.get("to"):
            warnings.append(f"flow #{i + 1} connects '{f.get('from')}' to itself and was skipped")
        fid = f.get("id") or f"flow-{f.get('from')}-{f.get('to')}"
        base, n = fid, 2
        while fid in fids or fid in ids:
            fid, n = f"{base}-{n}", n + 1
        fids.add(fid)
        f["_id"] = fid
    if errors:
        raise SpecError("\n".join(errors))
    flows = [f for f in flows if f.get("from") != f.get("to")]

    # ---- layers
    names = list(spec.get("layers") or [])
    for c in comps:
        lay = c.get("layer")
        if isinstance(lay, str) and names and lay not in names:
            raise SpecError(f"component '{c['id']}' uses layer '{lay}', which is not in the layers list {names}")
    if any(isinstance(c.get("layer"), str) for c in comps) and not names:
        names = []
        for c in comps:
            if isinstance(c.get("layer"), str) and c["layer"] not in names:
                names.append(c["layer"])
        warnings.append("no 'layers' list given; layer order taken from first appearance: " + ", ".join(names))
    idx = {}
    for c in comps:
        lay = c.get("layer")
        if isinstance(lay, str):
            idx[c["id"]] = names.index(lay)
        elif isinstance(lay, int):
            idx[c["id"]] = lay
    missing = [c["id"] for c in comps if c["id"] not in idx]
    if missing:
        if len(missing) == len(comps):
            idx = _longest_path(comps, flows)
            warnings.append("no layers given; layers inferred from the flow direction")
        else:
            for _ in range(len(comps)):
                for cid in missing:
                    preds = [idx[f["from"]] for f in flows if f["to"] == cid and f["from"] in idx]
                    succs = [idx[f["to"]] for f in flows if f["from"] == cid and f["to"] in idx]
                    if preds:
                        idx[cid] = max(preds) + 1
                    elif succs:
                        idx[cid] = max(0, min(succs) - 1)
            for cid in missing:
                idx.setdefault(cid, 0)
            warnings.append("components without a layer were placed next to their neighbours: " + ", ".join(missing))
    used = sorted(set(idx.values()))
    remap = {old: new for new, old in enumerate(used)}
    dropped = [names[i] for i in range(len(names)) if i not in remap] if names else []
    if dropped:
        warnings.append("empty layers were dropped: " + ", ".join(dropped))
    layer_names = [names[i] if names and i < len(names) else None for i in used]
    for c in comps:
        c["_layer"] = remap[idx[c["id"]]]
    isolated = [c["id"] for c in comps if not any(c["id"] in (f["from"], f["to"]) for f in flows)]
    if isolated and flows:
        warnings.append("components with no flows (check nothing is missing): " + ", ".join(isolated))
    # on a slide the slide's own title does this job, so the canvas doesn't repeat it
    show_title = spec.get("show_title", target != "slide")
    return {"title": spec.get("title") if show_title else None,
            "subtitle": spec.get("subtitle") if show_title else None,
            "page_name": spec.get("title"), "target": target, "direction": direction,
            "style": style, "components": comps, "boundaries": bounds, "flows": flows,
            "layer_names": layer_names, "show_layers": spec.get("show_layers", bool(names)),
            "legend": spec.get("legend", "auto")}, warnings


def _longest_path(comps, flows):
    order = [c["id"] for c in comps]
    succ = {c: [] for c in order}
    for f in flows:
        succ[f["from"]].append(f["to"])
    state, back = {}, set()

    def dfs(u):
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                dfs(v)
        state[u] = 2
    for c in order:
        if c not in state:
            dfs(c)
    layer = {c: 0 for c in order}
    for _ in range(len(order)):
        changed = False
        for f in flows:
            if (f["from"], f["to"]) in back:
                continue
            if layer[f["to"]] < layer[f["from"]] + 1:
                layer[f["to"]] = layer[f["from"]] + 1
                changed = True
        if not changed:
            break
    return layer


def wrap_words(text, max_chars):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


# ------------------------------------------------------------------ build
def build(spec, lib=None, theme=None):
    """Return (tree, report). `spec` is a dict (already loaded JSON)."""
    lib = lib or Library()
    s, warnings = normalize_spec(json.loads(json.dumps(spec)))
    th = theme or theme_for(s["target"])
    if spec.get("show_details") is False:
        for c in s["components"]:
            c["detail"] = False
    T, sz, sp = th, th["sizes"], th["spacing"]

    # ---- icons
    res = {}
    for c in s["components"]:
        r = lib.resolve(c.get("tech"), c.get("type"))
        res[c["id"]] = r
    with_brand = sum(1 for r in res.values() if r.kind in ("exact", "alias", "stripped", "fallback", "found"))
    style = s["style"]
    if style == "auto":
        avg_len = sum(len(c["label"]) for c in s["components"]) / len(s["components"])
        statuses = any(c.get("status") for c in s["components"])
        all_icons = all(r.icon_key for r in res.values())
        style = "node" if (not statuses and all_icons and with_brand >= 0.7 * len(res)
                           and avg_len <= 16 and len(res) <= 16) else "card"
    use_icons = style != "none"

    # ---- sizes
    card, nodecfg = T["card"], T["node"]
    nodes_in, label_lines = {}, {}
    if style in ("card", "none"):
        icon_space = (card["icon_pad"] + card["icon"] + 10) if use_icons else 14
        widths = []
        for c in s["components"]:
            detail = _detail(c, res[c["id"]], lib)
            tw = max(text_width(c["label"], sz["name"], True), text_width(detail or "", sz["detail"]))
            widths.append(icon_space + tw + 16)
        width = max(card["min_width"], min(card["max_width"], max(widths)))
        width = int(-(-width // 10) * 10)
        avail = width - icon_space - 12
        height = card["height"]
        for c in s["components"]:
            lines = max(1, round(text_width(c["label"], sz["name"], True) / avail + 0.45))
            extra = 1 if _detail(c, res[c["id"]], lib) else 0
            height = max(height, int(-(-(16 + (lines + extra) * 15 + 6) // 10) * 10))
        for i, c in enumerate(s["components"]):
            nodes_in[c["id"]] = {"layer": c["_layer"], "boundary": c.get("boundary"), "order": i,
                                 "box": (width, height), "cell": (0, 0, width, height)}
    else:
        ic = nodecfg["icon"]
        nf, df = sz["name"] - 1, sz["detail"]          # 11 / 10 on screen; larger for slides
        for i, c in enumerate(s["components"]):
            lines = wrap_words(c["label"], int(18 * 11 / nf))
            detail = _detail(c, res[c["id"]], lib)
            label_lines[c["id"]] = (lines, detail)
            lw = max([text_width(ln, nf, True) for ln in lines] + [text_width(detail or "", df)])
            bw = max(nodecfg["min_label_width"], min(nodecfg["max_label_width"] * nf / 11, lw + 12))
            bw = int(-(-bw // 10) * 10)
            bh = int(-(-(ic + 6 + round(nf * 1.27) * len(lines) + (round(df * 1.3) if detail else 0)) // 10) * 10)
            nodes_in[c["id"]] = {"layer": c["_layer"], "boundary": c.get("boundary"), "order": i,
                                 "box": (bw, bh), "cell": ((bw - ic) / 2, 0, ic, ic)}
    b_in = {}
    for i, b in enumerate(s["boundaries"]):
        icon_key = _boundary_icon(b, lib, T) if use_icons else None
        b_in[b["id"]] = {"parent": b.get("parent"), "order": i, "icon": icon_key,
                         "label_w": text_width(b["label"], sz["boundary_label"], True)}
    e_in = []
    for f in s["flows"]:
        lab = str(f.get("label") or "").strip()
        lines = wrap_words(lab, 22) if lab else []
        box = None
        if lines:
            box = (max(text_width(ln, sz["edge_label"]) for ln in lines) + 10, 15 * len(lines) + 4)
        e_in.append({"id": f["_id"], "src": f["from"], "dst": f["to"], "label_box": box, "lines": lines})
    opts = {"direction": s["direction"], "layer_gap": sp["layer_gap"], "row_gap": sp["row_gap"],
            "pad": sp["boundary_pad"], "header": sp["boundary_header"], "lane": sp["lane"],
            "label_margin": sp["label_margin"], "grid": 10,
            "typical_c": (list(nodes_in.values())[0]["box"][1] if s["direction"] == "LR"
                          else list(nodes_in.values())[0]["box"][0])}
    lay = Layout(nodes_in, b_in, e_in, len(s["layer_names"]), opts)
    L = lay.run()
    warnings += L["warnings"]

    # ---- offsets: title, subtitle, layer labels
    m = sp["margin"]
    top = m + (32 if s["title"] else 0) + (22 if s["subtitle"] else 0)
    show_layers = s["show_layers"] and any(s["layer_names"])
    left_extra = 0
    if show_layers and s["direction"] == "LR":
        top += 30
    if show_layers and s["direction"] == "TB":
        left_extra = max(text_width((n or "").upper(), sz["layer_label"], True) for n in s["layer_names"]) + 40
    xs = [v["x"] for v in L["nodes"].values()] + [v["x"] for v in L["boundaries"].values()]
    ys = [v["y"] for v in L["nodes"].values()] + [v["y"] for v in L["boundaries"].values()]
    for e in L["edges"].values():
        xs += [p[0] for p in e["path"]]
        ys += [p[1] for p in e["path"]]
    dx = -(-(m + left_extra - min(xs)) // 10) * 10
    dy = -(-(top - min(ys)) // 10) * 10

    # ---- XML
    page_name = (s["page_name"] or "Architecture")[:60]
    tree, page = D.new_file(page_name, "arch-" + re.sub(r"[^a-z0-9]+", "-", page_name.lower()).strip("-")[:30])
    root = page.root
    abs_of = {}

    def add(cell):
        root.append(cell)
        return cell

    font = f"fontFamily={T['font_family']};"
    if s["title"]:
        add(D.make_vertex("diagram-title", D.esc(s["title"]),
                          f"text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;{font}fontSize={sz['title']};fontStyle=1;fontColor={T['text']};archRole=title;",
                          "1", m, m, 900, 30))
    if s["subtitle"]:
        add(D.make_vertex("diagram-subtitle", D.esc(s["subtitle"]),
                          f"text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;{font}fontSize={sz['subtitle']};fontColor={T['muted']};archRole=subtitle;",
                          "1", m, m + (32 if s["title"] else 0), 900, 20))
    if show_layers:
        for k, name in enumerate(s["layer_names"]):
            if not name:
                continue
            ly = L["layers"][k]
            if s["direction"] == "LR":
                add(D.make_vertex(f"layer-{k}", D.esc(name.upper()),
                                  f"text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;{font}fontSize={sz['layer_label']};fontStyle=1;fontColor={T['muted']};archRole=layer-label;",
                                  "1", ly["x"] + dx, top - 30, ly["w"], 20))
            else:
                add(D.make_vertex(f"layer-{k}", D.esc(name.upper()),
                                  f"text;html=1;align=right;verticalAlign=middle;whiteSpace=wrap;{font}fontSize={sz['layer_label']};fontStyle=1;fontColor={T['muted']};archRole=layer-label;",
                                  "1", m, ly["y"] + dy + ly["h"] / 2 - 10, left_extra - 16, 20))

    # boundaries, outermost first
    bmap = {b["id"]: b for b in s["boundaries"]}
    drawn_b = [b for b in s["boundaries"] if b["id"] in L["boundaries"]]
    depth = {}
    for b in drawn_b:
        d, cur = 0, b.get("parent")
        while cur:
            d, cur = d + 1, bmap[cur].get("parent")
        depth[b["id"]] = d
    for b in sorted(drawn_b, key=lambda b: depth[b["id"]]):
        r = L["boundaries"][b["id"]]
        ax, ay = r["x"] + dx, r["y"] + dy
        abs_of[b["id"]] = (ax, ay)
        parent = b.get("parent") or "1"
        px, py = abs_of.get(parent, (0, 0))
        kind = str(b.get("kind") or "generic").lower()
        bs = T["boundaries"].get(kind, T["boundaries"]["generic"])
        icon_key = b_in[b["id"]]["icon"]
        bstyle = (f"rounded=1;absoluteArcSize=1;arcSize=14;whiteSpace=wrap;html=1;container=1;collapsible=0;"
                  f"recursiveResize=0;fillColor={bs['fill']};strokeColor={bs['stroke']};dashed={bs['dashed']};"
                  f"strokeWidth=1.2;verticalAlign=top;align=left;spacingLeft={40 if icon_key else 14};spacingTop=6;"
                  f"{font}fontSize={sz['boundary_label']};fontStyle=1;fontColor=#374151;"
                  f"archRole=boundary;archKind={kind};")
        add(D.make_vertex(b["id"], D.esc(b["label"]), bstyle, parent, ax - px, ay - py, r["w"], r["h"]))
        if icon_key:
            add(D.make_vertex(f"{b['id']}-icon", "",
                              f"shape=image;html=1;imageAspect=1;aspect=fixed;image={lib.data_uri(icon_key)};connectable=0;archRole=icon;archIcon={icon_key};",
                              b["id"], 12, 7, 22, 22))

    # components
    status_pal = T["status"]
    icon_notes = []
    for c in s["components"]:
        n = L["nodes"][c["id"]]
        r = res[c["id"]]
        parent = c.get("boundary") or "1"
        px, py = abs_of.get(parent, (0, 0))
        st = status_pal.get(str(c.get("status") or "").lower())
        if c.get("status") and not st:
            warnings.append(f"component '{c['id']}' has unknown status '{c['status']}' (use {', '.join(status_pal)})")
        meta = f"archRole=component;archType={_safe(c.get('type'))};archTech={_safe(c.get('tech'))};"
        detail = _detail(c, r, lib)
        if r.note:
            icon_notes.append(f"{c['id']}: {r.note}")
        if style in ("card", "none"):
            fill = st["fill"] if st else T["card"]["fill"]
            stroke = st["stroke"] if st else T["card"]["stroke"]
            has_icon = use_icons and r.icon_key
            value = f"<b>{D.esc(c['label'])}</b>" + (
                f"<br><span style=\"font-size:{sz['detail']}px;color:{T['muted']};\">{D.esc(detail)}</span>" if detail else "")
            cstyle = (f"rounded=1;absoluteArcSize=1;arcSize={card['radius']};whiteSpace=wrap;html=1;"
                      f"fillColor={fill};strokeColor={stroke};strokeWidth=1.2;{'dashed=1;' if st and st.get('dashed') else ''}"
                      f"{font}fontSize={sz['name']};fontColor={T['text']};align=left;verticalAlign=middle;"
                      f"spacingLeft={card['icon_pad'] + card['icon'] + 8 if has_icon else 14};spacingRight=8;"
                      f"collapsible=0;{meta}")
            add(D.make_vertex(c["id"], value, cstyle, parent, n["x"] + dx - px, n["y"] + dy - py, n["w"], n["h"]))
            if has_icon:
                ic = card["icon"]
                add(D.make_vertex(f"{c['id']}-icon", "",
                                  f"shape=image;html=1;imageAspect=1;aspect=fixed;image={lib.data_uri(r.icon_key)};connectable=0;archRole=icon;archIcon={r.icon_key};",
                                  c["id"], card["icon_pad"], (n["h"] - ic) / 2, ic, ic))
        else:
            lines, det = label_lines[c["id"]]
            value = "<br>".join(f"<b>{D.esc(ln)}</b>" for ln in lines) + (
                f"<br><span style=\"font-size:{sz['detail']}px;color:{T['muted']};\">{D.esc(det)}</span>" if det else "")
            key = r.icon_key or "generic-service"
            if not r.icon_key:
                icon_notes.append(f"{c['id']}: no type or known technology; shown with the generic service icon")
            extra = f"imageBackground={st['fill']};imageBorder={st['stroke']};" if st else ""
            cstyle = (f"shape=image;html=1;imageAspect=1;aspect=fixed;image={lib.data_uri(key)};{extra}"
                      f"verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
                      f"{font}fontSize={sz['name'] - 1};fontColor={T['text']};spacingTop=0;{meta}archIcon={key};")
            add(D.make_vertex(c["id"], value, cstyle, parent, n["cx"] + dx - px, n["cy"] + dy - py, n["cw"], n["ch"]))

    # flows
    ec = T["edge"]
    cparent = {c["id"]: c.get("boundary") for c in s["components"]}

    def chain(bid):
        out = []
        while bid:
            out.append(bid)
            bid = bmap[bid].get("parent")
        return out
    used_async = False
    for f, e_spec in zip(s["flows"], e_in):
        e = L["edges"][f["_id"]]
        ca, cb = chain(cparent[f["from"]]), chain(cparent[f["to"]])
        common = next((b for b in ca if b in cb), None)
        parent = common or "1"
        px, py = abs_of.get(parent, (0, 0))
        asyn = str(f.get("style", "sync")).lower() in ("async", "event", "events", "message")
        used_async |= asyn
        (ex, ey), (nx, ny) = e["exit"], e["entry"]
        # jumpStyle=arc: where two connectors cross, the later one hops over in a small U-bend
        estyle = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;jumpStyle=arc;jumpSize=8;"
                  f"endArrow=block;endFill=1;endSize=6;strokeColor={ec['color']};strokeWidth={ec['width']};"
                  f"{'dashed=1;dashPattern=6 4;' if asyn else ''}"
                  f"{'startArrow=block;startFill=1;startSize=6;' if f.get('bidirectional') else ''}"
                  f"{font}fontSize={sz['edge_label']};fontColor={ec['label_color']};labelBackgroundColor=#ffffff;"
                  f"exitX={_f(ex)};exitY={_f(ey)};exitDx=0;exitDy={_f(round(e['exit_dy']))};"
                  f"entryX={_f(nx)};entryY={_f(ny)};entryDx=0;entryDy={_f(round(e['entry_dy']))};"
                  f"archRole=flow;archFlow={'async' if asyn else 'sync'};")
        pts = [(round(x + dx - px), round(y + dy - py)) for x, y in e["points"]]
        value = "<br>".join(D.esc(ln) for ln in e_spec["lines"])
        add(D.make_edge(f["_id"], value, estyle, parent, f["from"], f["to"], pts, e["label_pos"]))

    # legend
    statuses = []
    for c in s["components"]:
        k = str(c.get("status") or "").lower()
        if k in status_pal and k not in statuses:
            statuses.append(k)
    want_legend = s["legend"] is True or (s["legend"] == "auto" and (statuses or used_async))
    bottom = max(ys) + dy
    for e in L["edges"].values():
        bottom = max([bottom] + [p[1] + dy for p in e["path"]])
    bottom = max([bottom] + [L["nodes"][c]["y"] + dy + L["nodes"][c]["h"] for c in L["nodes"]] +
                 [L["boundaries"][b]["y"] + dy + L["boundaries"][b]["h"] for b in L["boundaries"]])
    content_right = max([L["nodes"][c]["x"] + dx + L["nodes"][c]["w"] for c in L["nodes"]] +
                        [L["boundaries"][b]["x"] + dx + L["boundaries"][b]["w"] for b in L["boundaries"]] +
                        [p[0] + dx for e in L["edges"].values() for p in e["path"]])
    page_h = bottom + m
    if want_legend:
        entries = [("status", k) for k in statuses] + ([("line", "sync"), ("line", "async")] if used_async else [])
        lh = 30 + 22 * len(entries) + 8
        lw = 210
        lx, ly = m, bottom + 30
        area = T.get("targets", {}).get(s["target"], {}).get("area_px")
        if area:
            # below or beside, whichever lets the whole picture fit the slide/page larger
            cw, ch = content_right - m, bottom - top
            below = min(area[0] / max(cw, lw), area[1] / (ch + 30 + lh))
            beside = min(area[0] / (cw + 30 + lw), area[1] / max(ch, lh))
            if beside > below:
                lx, ly = content_right + 30, bottom - lh
                content_right += 30 + lw
        add(D.make_vertex("legend", "Legend",
                          f"rounded=1;absoluteArcSize=1;arcSize=10;whiteSpace=wrap;html=1;container=1;collapsible=0;recursiveResize=0;"
                          f"fillColor=#ffffff;strokeColor=#cbd5e1;verticalAlign=top;align=left;spacingLeft=12;spacingTop=4;"
                          f"{font}fontSize={sz['legend']};fontStyle=1;fontColor=#374151;archRole=legend;",
                          "1", lx, ly, lw, lh))
        for i, (kind, k) in enumerate(entries):
            y = 30 + 22 * i
            if kind == "status":
                st = status_pal[k]
                add(D.make_vertex(f"legend-{k}", "",
                                  f"rounded=1;absoluteArcSize=1;arcSize=6;html=1;fillColor={st['fill']};strokeColor={st['stroke']};{'dashed=1;' if st.get('dashed') else ''}archRole=legend-item;",
                                  "legend", 12, y + 2, 34, 14))
                text = st["label"]
            else:
                cell = D.make_edge(f"legend-{k}", "",
                                   f"endArrow=block;endFill=1;endSize=5;html=1;strokeColor={ec['color']};strokeWidth={ec['width']};"
                                   f"{'dashed=1;dashPattern=6 4;' if k == 'async' else ''}archRole=legend-item;",
                                   "legend", None, None)
                del cell.attrib["source"], cell.attrib["target"]
                g = cell.find("mxGeometry")
                import xml.etree.ElementTree as ET
                ET.SubElement(g, "mxPoint", {"x": "12", "y": str(y + 9), "as": "sourcePoint"})
                ET.SubElement(g, "mxPoint", {"x": "46", "y": str(y + 9), "as": "targetPoint"})
                add(cell)
                text = "Request / call" if k == "sync" else "Event / async message"
            add(D.make_vertex(f"legend-{k}-text", D.esc(text),
                              f"text;html=1;align=left;verticalAlign=middle;{font}fontSize={sz['legend']};fontColor={T['text']};archRole=legend-item;",
                              "legend", 56, y, lw - 64, 18))
        page_h = max(page_h, ly + lh + m)
    model = page.model
    model.set("pageWidth", str(int(max(content_right + m, 600))))
    model.set("pageHeight", str(int(max(page_h, 400))))
    page.reindex()
    report = {"style": style, "direction": s["direction"], "warnings": warnings, "icon_notes": icon_notes,
              "stats": L["stats"], "components": len(s["components"]), "flows": len(s["flows"]),
              "boundaries": len(L["boundaries"]),
              "icons": {c["id"]: (res[c["id"]].icon_key, res[c["id"]].kind) for c in s["components"]},
              "target": s["target"], "page": page}
    return tree, report


def build_for_target(spec, lib=None):
    """Build for where the diagram will be shown. For a slide or document page, every layout
    the spec leaves open (direction LR/TB, icon style card/node) is built with ALL components,
    and the one whose smallest text stays largest on the target wins. Nothing is dropped to fit."""
    from .readability import measure, score
    lib = lib or Library()
    target = str(spec.get("target", "screen")).lower()
    th = theme_for(target)
    fixed = target == "screen"
    dirs = [spec.get("direction")] if spec.get("direction") or fixed else ["LR", "TB"]
    style = str(spec.get("icon_style", "auto")).lower()
    styles = [spec.get("icon_style")] if style not in ("auto", "") or fixed else ["card", "node"]
    best, tried = None, []
    for d in dirs:
        for st in styles:
            trial = dict(spec)
            if d:
                trial["direction"] = d
            if st:
                trial["icon_style"] = st
            tree, rep = build(trial, lib, th)
            m = measure(rep["page"], target, th)
            tried.append((f"{rep['direction']}/{rep['style']}", m))
            if best is None or score(m) > score(best[2]):
                best = (tree, rep, m)
    tree, rep, m = best
    rep["readability"], rep["tried"] = m, tried
    cfg = th.get("targets", {}).get(target, {})
    if not fixed and m.get("min_pt", 99) < cfg.get("good_pt", 0) and "show_details" not in spec:
        # a content choice, so only reported: what the best layout gives with names only (all boxes kept)
        _, rep2 = build(dict(spec, direction=rep["direction"], icon_style=rep["style"], show_details=False), lib, th)
        rep["without_details"] = measure(rep2["page"], target, th).get("min_pt")
    rep["target_cfg"] = th.get("targets", {}).get(target, {})
    return tree, rep


def _detail(c, r, lib):
    if c.get("detail") is False:          # no second line (spec "show_details": false)
        return ""
    if c.get("detail"):
        return str(c["detail"])
    if r.tech_key and normalize(lib.label(r.tech_key)) not in normalize(c["label"]):
        return lib.label(r.tech_key)
    if c.get("tech") and not r.tech_key and normalize(str(c["tech"])) not in normalize(c["label"]):
        return str(c["tech"])
    return ""


def _boundary_icon(b, lib, T):
    if b.get("tech"):
        r = lib.resolve(b["tech"], None)
        if r.icon_key:
            return r.icon_key
    key = T["boundary_icons"].get(str(b.get("kind") or "").lower())
    if key and lib.available(key):
        return key
    if key and key in lib.entries:
        fallback = {"aws": "generic-cloud", "azure": "generic-cloud", "gcp": "generic-cloud",
                    "kubernetes": "generic-cluster", "docker": "generic-container"}.get(key)
        if fallback and lib.available(fallback):
            return fallback
    return None


def _safe(v):
    return re.sub(r"[;=]", " ", str(v or "")).strip()


def _f(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
