"""Validate a .drawio file. Errors mean the file is wrong; warnings mean it probably looks wrong."""
import base64
import binascii
import re

from . import drawio as D
from .drawio import parse_style, shape_of, label_text
from .geometry import edge_path, seg_hits_rect
from .layout import text_width

CONTAINER_ROLES = {"boundary", "legend"}


def is_container(page, cid):
    names, st = parse_style(page.style(cid))
    return (st.get("container") == "1" or st.get("archRole") in CONTAINER_ROLES or "swimlane" in names
            or "group" in names or st.get("shape") == "swimlane")


def is_text(page, cid):
    names, st = parse_style(page.style(cid))
    return "text" in names or st.get("shape") == "text" or st.get("archRole") in (
        "title", "subtitle", "layer-label") or (st.get("strokeColor") == "none" and st.get("fillColor") in (None, "none")
                                                and shape_of(page.style(cid)) == "rect" and not page.children.get(cid))


def visual_containers(page, rects):
    """Plain rectangles used as boundaries: they enclose other shapes but are not draw.io containers."""
    out = set()
    for v, r in rects.items():
        if is_text(page, v) or is_container(page, v) or shape_of(page.style(v)) not in ("rect", "label"):
            continue
        st = parse_style(page.style(v))[1]
        kids = set(page.descendants(v))
        inside = [o for o, ro in rects.items() if o != v and o not in kids and not is_text(page, o)
                  and parse_style(page.style(o))[1].get("archRole") != "icon" and shape_of(page.style(o)) != "image"
                  and _contains(r, ro) and ro[2] * ro[3] < r[2] * r[3]]
        if inside and (st.get("fillColor") in ("none", None) or st.get("dashed") == "1" or len(inside) >= 2):
            out.add(v)
    return out


def _luminance(hexcolor):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", hexcolor or "")
    if not m:
        return None
    vals = [int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in vals]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(c1, c2):
    l1, l2 = _luminance(c1), _luminance(c2)
    if l1 is None or l2 is None:
        return None
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def check_page(page, lib=None):
    errors, warnings, info = [], [], []
    ids = [cid for cid, _, _ in page.items]
    if not any(c.get("parent") is None for _, _, c in page.items):
        errors.append("no root cell (id 0)")
    seen = set()
    for cid in ids:
        if not cid:
            errors.append("a cell has no id")
        elif cid in seen:
            errors.append(f"duplicate id '{cid}'")
        seen.add(cid)
    for cid, _, cell in page.items:
        p = cell.get("parent")
        if p is not None and p not in page.by_id:
            errors.append(f"'{cid}' has parent '{p}', which doesn't exist")
    # parent loops
    for cid in page.by_id:
        cur, hops = cid, 0
        while cur in page.by_id and page.cell(cur).get("parent") is not None and hops < 200:
            cur, hops = page.cell(cur).get("parent"), hops + 1
        if hops >= 200:
            errors.append(f"'{cid}' is inside itself (parent loop)")
    verts = [c for c in page.by_id if page.is_vertex(c)]
    edges = [c for c in page.by_id if page.is_edge(c)]
    rects = {}
    for v in verts:
        r = page.rel_rect(v)
        parent = page.cell(v).get("parent")
        if r is None:
            if parent in page.by_id and page.is_edge(parent):
                continue          # edge label
            errors.append(f"shape '{v}' has no geometry")
            continue
        if r[2] <= 0 or r[3] <= 0:
            errors.append(f"shape '{v}' has zero size")
            continue
        rects[v] = page.abs_rect(v)
    # styles
    for cid in page.by_id:
        for part in (page.style(cid) or "").split(";"):
            if part.strip().startswith("="):
                warnings.append(f"'{cid}' has a malformed style entry '{part}'")
    # images
    bounds = _bounds(rects.values())
    for v in rects:
        st = parse_style(page.style(v))[1]
        img = st.get("image")
        if not img:
            continue
        if img.startswith("http://") or img.startswith("https://"):
            warnings.append(f"'{v}' loads its image from {img[:60]}...; it won't show offline. Embed it instead")
        elif img.startswith("data:"):
            payload = img.split(",", 1)[1] if "," in img else ""
            try:
                raw = base64.b64decode(payload, validate=True)
                if img.startswith("data:image/svg") and b"<svg" not in raw[:4000]:
                    errors.append(f"'{v}' has an embedded image that isn't an SVG")
            except (binascii.Error, ValueError):
                errors.append(f"'{v}' has a corrupt embedded image")
        r = rects[v]
        if bounds and r[2] * r[3] > 0.6 * (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) and len(rects) < 6:
            warnings.append(f"'{v}' is an image covering most of the diagram; the diagram may be a flattened picture rather than editable shapes")
    # editability
    for v in rects:
        st = parse_style(page.style(v))[1]
        locked = [k for k in ("locked", "movable", "editable", "deletable") if st.get(k) in ("1" if k == "locked" else "0",)]
        if locked and st.get("archRole") not in ("icon", "legend-item"):
            warnings.append(f"'{v}' is not fully editable ({', '.join(locked)})")
    # overlaps and false containment
    solid = [v for v in rects if not (page.cell(v).get("parent") in page.by_id and page.is_edge(page.cell(v).get("parent")))]
    visual = visual_containers(page, rects)
    anc = {v: set(page.ancestors(v)) for v in solid}
    for i, a in enumerate(solid):
        for b in solid[i + 1:]:
            if a in anc[b] or b in anc[a]:
                continue
            ra, rb = rects[a], rects[b]
            ov = _overlap(ra, rb)
            if ov <= 4:
                continue
            a_in_b, b_in_a = _contains(rb, ra), _contains(ra, rb)
            if b_in_a and is_container(page, a):
                warnings.append(f"'{_name(page, b)}' sits inside container '{_name(page, a)}' but is not its member")
            elif a_in_b and is_container(page, b):
                warnings.append(f"'{_name(page, a)}' sits inside container '{_name(page, b)}' but is not its member")
            elif a_in_b or b_in_a:
                big, small = (b, a) if a_in_b else (a, b)
                if big in visual:
                    continue
                if not is_text(page, small) and not is_text(page, big):
                    info.append(f"'{_name(page, small)}' is drawn on top of '{_name(page, big)}'")
            else:
                warnings.append(f"'{_name(page, a)}' and '{_name(page, b)}' overlap")
    # connectors
    obstacles = [v for v in solid if not is_container(page, v) and v not in visual
                 and parse_style(page.style(v))[1].get("archRole") not in ("icon", "legend-item", "layer-label")]
    crossings_through = 0
    paths = {}
    for e in edges:
        cell = page.cell(e)
        src, tgt = cell.get("source"), cell.get("target")
        for end, ref in (("source", src), ("target", tgt)):
            if ref is not None and ref not in page.by_id:
                errors.append(f"connector '{e}' has {end} '{ref}', which doesn't exist")
            elif ref is not None and page.is_edge(ref):
                info.append(f"connector '{e}' attaches to another connector")
        if parse_style(page.style(e))[1].get("archRole") == "legend-item":
            continue
        if not src or not tgt:
            warnings.append(f"connector '{_name(page, e)}' is not attached at both ends")
            continue
        if src not in rects or tgt not in rects:
            continue
        pts, meta = edge_path(page, e)
        paths[e] = pts
        skip = {src, tgt} | set(page.ancestors(src)) | set(page.ancestors(tgt)) | set(page.descendants(src)) | set(page.descendants(tgt))
        hit = []
        for a, b in zip(pts, pts[1:]):
            for o in obstacles:
                if o in skip:
                    continue
                if seg_hits_rect(a, b, rects[o], inset=2):
                    hit.append(o)
        for o in dict.fromkeys(hit):
            crossings_through += 1
            warnings.append(f"connector {_ename(page, e)} passes through '{_name(page, o)}'")
    crossings = _count_crossings(paths)
    from .lines import has_jumps, label_rect, overlaps, through_labels
    labels = {e: label_rect(page, e, pts) for e, pts in paths.items()}
    for e, f in through_labels(paths, labels):
        warnings.append(f"connector {_ename(page, e)} runs through the label of {_ename(page, f)}")
    ov = overlaps(paths)
    if ov:
        shown = "; ".join(f"{_ename(page, a)} / {_ename(page, b)} ({n} px)" for a, b, n in ov[:6])
        warnings.append(f"{len(ov)} place(s) where connectors run on top of each other: {shown}"
                        + (" ..." if len(ov) > 6 else ""))
    if crossings:
        bare = [e for e in paths if not has_jumps(page, e)]
        info.append(f"{crossings} connector crossing(s)" +
                    ("; drawn as U-bend line jumps" if not bare else
                     f"; {len(bare)} connector(s) have no line jumps, so crossings look like junctions "
                     f"(tidy-lines adds them)"))
    # readability
    for v in rects:
        names, st = parse_style(page.style(v))
        role = st.get("archRole")
        if role in ("icon", "legend-item"):
            continue
        text = label_text(page.value(v))
        if not text:
            continue
        fs = float(st.get("fontSize", 11))
        if fs < 9:
            warnings.append(f"'{_name(page, v)}' uses a {fs:g}px font, too small to read")
        fill, fc = st.get("fillColor"), st.get("fontColor", "#000000")
        if fill and fill not in ("none", "default") and "image" not in names and st.get("shape") != "image":
            ratio = _contrast(fill, fc)
            if ratio is not None and ratio < 3:
                warnings.append(f"'{_name(page, v)}': text colour {fc} on {fill} is hard to read")
        r = rects[v]
        if st.get("whiteSpace") == "wrap" and not is_container(page, v) and st.get("verticalLabelPosition") != "bottom":
            avail = r[2] - float(st.get("spacingLeft", 2)) - float(st.get("spacingRight", 2)) - 4
            lines = sum(max(1, -(-text_width(ln, fs, True) // max(avail, 10))) for ln in text.split("\n"))
            if lines * fs * 1.3 > r[3] + 6:
                warnings.append(f"'{_name(page, v)}': label probably overflows its shape ({int(lines)} lines in {int(r[3])}px)")
    # icon correctness
    if lib is not None:
        for v in rects:
            st = parse_style(page.style(v))[1]
            if st.get("archRole") == "icon":
                parent = page.cell(v).get("parent")
                pst = parse_style(page.style(parent))[1] if parent in page.by_id else {}
                key = st.get("archIcon")
                if not key:
                    warnings.append(f"icon '{v}' has no archIcon key, so its technology can't be verified")
                    continue
                if pst.get("archRole") == "component" and (pst.get("archTech") or pst.get("archType")):
                    allowed = lib.allowed_icons(pst.get("archTech") or None, pst.get("archType") or None)
                    if key not in allowed:
                        errors.append(f"'{_name(page, parent)}' (technology '{pst.get('archTech') or pst.get('archType')}') shows the '{key}' icon")
                    else:
                        best = lib.resolve(pst.get("archTech") or None, pst.get("archType") or None, create=False).icon_key
                        if best and best != key:
                            info.append(f"'{_name(page, parent)}' could use the '{best}' icon now (run apply-icons --refresh)")
            elif st.get("archRole") == "component" and st.get("archIcon") and lib is not None:
                allowed = lib.allowed_icons(st.get("archTech") or None, st.get("archType") or None) | {"generic-service"}
                if st["archIcon"] not in allowed:
                    errors.append(f"'{_name(page, v)}' (technology '{st.get('archTech')}') shows the '{st['archIcon']}' icon")
    return {"errors": errors, "warnings": warnings, "info": info, "shapes": len(rects),
            "connectors": len(edges), "crossings": crossings}


def check_spec(page, spec):
    errors, warnings = [], []
    for c in spec.get("components", []):
        if c["id"] not in page.by_id or not page.is_vertex(c["id"]):
            errors.append(f"component '{c['id']}' from the spec is missing")
        elif label_text(page.value(c["id"])).split("\n")[0].strip() != str(c["label"]).strip() and \
                str(c["label"]).strip() not in label_text(page.value(c["id"])).replace("\n", " "):
            warnings.append(f"component '{c['id']}' label differs from the spec")
    edges = {(page.cell(e).get("source"), page.cell(e).get("target")) for e in page.by_id if page.is_edge(e)}
    for f in spec.get("flows", []):
        if (f["from"], f["to"]) not in edges and not (f.get("bidirectional") and (f["to"], f["from"]) in edges):
            errors.append(f"flow {f['from']} -> {f['to']} from the spec is missing")
    for b in spec.get("boundaries", []):
        if b["id"] not in page.by_id:
            warnings.append(f"boundary '{b['id']}' from the spec is not drawn")
    for c in spec.get("components", []):
        if c.get("boundary") and c["id"] in page.by_id and c["boundary"] in page.by_id:
            if c["boundary"] not in page.ancestors(c["id"]):
                errors.append(f"component '{c['id']}' is not inside boundary '{c['boundary']}'")
    return errors, warnings


def compare(old_page, new_page):
    """Human-readable list of differences between two versions of a page."""
    out = []
    old_ids, new_ids = set(old_page.by_id), set(new_page.by_id)
    skip = lambda p, c: parse_style(p.style(c))[1].get("archRole") in ("icon", "legend-item") or c in p.layer_ids()
    for cid in sorted(old_ids - new_ids):
        if not skip(old_page, cid):
            out.append(f"REMOVED {_kind(old_page, cid)} '{cid}': {_desc(old_page, cid)}")
    for cid in sorted(new_ids - old_ids):
        if not skip(new_page, cid):
            out.append(f"ADDED   {_kind(new_page, cid)} '{cid}': {_desc(new_page, cid)}")
    for cid in sorted(old_ids & new_ids):
        if skip(new_page, cid):
            continue
        o, n = old_page.cell(cid), new_page.cell(cid)
        lo, ln = label_text(old_page.value(cid)), label_text(new_page.value(cid))
        if lo != ln:
            out.append(f"RELABEL '{cid}': {lo!r} -> {ln!r}")
        if o.get("parent") != n.get("parent"):
            out.append(f"MOVED INTO '{cid}': {o.get('parent')} -> {n.get('parent')}")
        if new_page.is_edge(cid):
            if (o.get("source"), o.get("target")) != (n.get("source"), n.get("target")):
                out.append(f"RECONNECTED '{cid}': {o.get('source')}->{o.get('target')} now {n.get('source')}->{n.get('target')}")
            else:
                op_, np_ = edge_path(old_page, cid)[0], edge_path(new_page, cid)[0]
                if len(op_) != len(np_) or any(abs(a[0] - b[0]) > 1 or abs(a[1] - b[1]) > 1 for a, b in zip(op_, np_)):
                    out.append(f"REROUTED '{cid}': {_desc(new_page, cid)}")
        else:
            ro, rn = old_page.abs_rect(cid), new_page.abs_rect(cid)
            if ro and rn and (abs(ro[0] - rn[0]) > 1 or abs(ro[1] - rn[1]) > 1):
                out.append(f"REPOSITIONED '{cid}' by ({rn[0] - ro[0]:+.0f}, {rn[1] - ro[1]:+.0f})")
            elif ro and rn and (abs(ro[2] - rn[2]) > 1 or abs(ro[3] - rn[3]) > 1):
                out.append(f"RESIZED '{cid}': {ro[2]:.0f}x{ro[3]:.0f} -> {rn[2]:.0f}x{rn[3]:.0f}")
        so, sn = parse_style(o.get("style", ""))[1], parse_style(n.get("style", ""))[1]
        changed = sorted(k for k in set(so) | set(sn) if so.get(k) != sn.get(k) and k not in ("image",)
                         and not k.startswith(("exit", "entry")))
        if changed:
            out.append(f"RESTYLED '{cid}': {', '.join(changed[:6])}")
    return out


def _kind(page, cid):
    if page.is_edge(cid):
        return "connector"
    return "container" if is_container(page, cid) else "shape"


def _desc(page, cid):
    if page.is_edge(cid):
        c = page.cell(cid)
        lab = label_text(page.value(cid)).replace("\n", " ")
        return f"{_name(page, c.get('source'))} -> {_name(page, c.get('target'))}" + (f" ({lab})" if lab else "")
    return _name(page, cid)


def _name(page, cid):
    if cid not in page.by_id:
        return str(cid)
    t = label_text(page.value(cid)).split("\n")[0]
    return t[:40] if t else cid


def _ename(page, e):
    c = page.cell(e)
    return f"'{_name(page, c.get('source'))}' -> '{_name(page, c.get('target'))}'"


def _overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0, x1 - x0) * max(0, y1 - y0)


def _contains(outer, inner, tol=1):
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and outer[0] + outer[2] + tol >= inner[0] + inner[2] and outer[1] + outer[3] + tol >= inner[1] + inner[3])


def _bounds(rects):
    rects = list(rects)
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects))


def _count_crossings(paths):
    segs = []
    for e, pts in paths.items():
        for a, b in zip(pts, pts[1:]):
            segs.append((e, a, b))
    n = 0
    for i in range(len(segs)):
        e1, a1, b1 = segs[i]
        for j in range(i + 1, len(segs)):
            e2, a2, b2 = segs[j]
            if e1 == e2:
                continue
            h1, h2 = abs(a1[1] - b1[1]) < 0.5, abs(a2[1] - b2[1]) < 0.5
            if h1 == h2:
                continue
            (ha, hb), (va, vb) = ((a1, b1), (a2, b2)) if h1 else ((a2, b2), (a1, b1))
            hx0, hx1 = sorted((ha[0], hb[0]))
            vy0, vy1 = sorted((va[1], vb[1]))
            if hx0 + 0.5 < va[0] < hx1 - 0.5 and vy0 + 0.5 < ha[1] < vy1 - 0.5:
                n += 1
    return n


def check_file(path, page_name=None, spec=None, compare_path=None, lib=None, log=print, baseline=False):
    try:
        tree, pages = D.load(path)
    except Exception as exc:
        log(f"ERROR: cannot read {path}: {exc}")
        return 1
    targets = [D.pick_page(pages, page_name)] if page_name else pages
    known = set()
    if baseline and compare_path:
        _, old_pages = D.load(compare_path)
        old_page = D.pick_page(old_pages, page_name) if page_name else old_pages[0]
        rb = check_page(old_page, lib)
        known = set(rb["errors"]) | set(rb["warnings"])
    total_errors = 0
    for page in targets:
        r = check_page(page, lib)
        errs, warns = list(r["errors"]), list(r["warnings"])
        pre = [m for m in errs + warns if m in known]
        errs = [m for m in errs if m not in known]
        warns = [m for m in warns if m not in known]
        if pre:
            r["info"].append(f"{len(pre)} issue(s) already present in the original were left as they were")
        if spec is not None:
            e2, w2 = check_spec(page, spec)
            errs += e2
            warns += w2
        total_errors += len(errs)
        log(f"[{page.name}] {r['shapes']} shapes, {r['connectors']} connectors, {r['crossings']} crossings: "
            f"{len(errs)} error(s), {len(warns)} warning(s)")
        for m in errs:
            log(f"  ERROR: {m}")
        for m in warns:
            log(f"  warning: {m}")
        for m in r["info"]:
            log(f"  info: {m}")
    if compare_path:
        _, old_pages = D.load(compare_path)
        old = D.pick_page(old_pages, page_name) if page_name else old_pages[0]
        new = targets[0]
        diff = compare(old, new)
        log(f"\nChanges compared with {compare_path} ({len(diff)}):")
        for line in diff or ["(no changes)"]:
            log("  " + line)
    return 1 if total_errors else 0
