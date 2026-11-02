"""Readability of a draw.io page for where it will be shown: a 16:9 slide, a document page or a screen.

The key number is the smallest text size in points once the whole diagram is scaled to fit
the target area. A diagram that is fine on screen can shrink to 5 pt on a slide; this says so
before anyone pastes it into PowerPoint, together with how much of the canvas is empty.
"""
import re

from . import drawio as D

TARGETS = ("slide", "document", "screen")
PX_TO_PT = 0.75


def target_cfg(theme, target):
    return (theme.get("targets") or {}).get(target or "screen", {})


def _font_sizes(page, cid):
    """Text sizes used by a cell: its fontSize plus any inline font-size spans."""
    st = D.parse_style(page.style(cid))[1]
    sizes = [float(st.get("fontSize", 11))]
    sizes += [float(s) for s in re.findall(r"font-size:\s*([\d.]+)px", page.value(cid) or "")]
    return sizes


def _boundary_ids(page):
    out = set()
    for cid, _, cell in page.items:
        st = D.parse_style(cell.get("style", ""))[1]
        if st.get("archRole") in ("boundary", "legend") or st.get("container") == "1":
            out.add(cid)
    return out


def measure(page, target="screen", theme=None):
    """Numbers and advice for one page. `theme` supplies the target's area and limits."""
    cfg = target_cfg(theme or {}, target)
    rects, comp_area, words, min_px, comps, flows = [], 0.0, 0, None, 0, 0
    containers = _boundary_ids(page)
    for cid, _, cell in page.items:
        if cell.get("vertex") == "1" and cid in page.by_id:
            try:
                r = page.abs_rect(cid)
            except Exception:
                continue
            if r[2] <= 0 or r[3] <= 0:
                continue
            rects.append(r)
            st = D.parse_style(cell.get("style", ""))[1]
            text = D.label_text(page.value(cid))
            if text:
                words += len(re.findall(r"\w+", text))
                if not st.get("archRole", "").startswith("legend"):
                    m = min(_font_sizes(page, cid))
                    min_px = m if min_px is None else min(min_px, m)
            role = st.get("archRole")
            # content area: components, plus unlabelled-by-the-skill shapes that carry text (hand-made diagrams)
            plain_box = not role and text and cid not in containers and not cell.get("style", "").startswith("text")
            if role == "component" or plain_box:
                area = r[2] * r[3]
                if st.get("verticalLabelPosition") == "bottom":     # icon node: count its label under the icon
                    lines = max(1, len(text.split("\n")))
                    area = max(r[2], 100) * (r[3] + 15 * lines)
                comp_area += area
                comps += 1
        elif cell.get("edge") == "1":
            st = D.parse_style(cell.get("style", ""))[1]
            if st.get("archRole") != "legend-item":
                flows += 1
                text = D.label_text(page.value(cid))
                if text:
                    words += len(re.findall(r"\w+", text))
                    m = min(_font_sizes(page, cid))
                    min_px = m if min_px is None else min(min_px, m)
    if not rects:
        return {"target": target, "empty": True}
    x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects); y1 = max(r[1] + r[3] for r in rects)
    w, h = x1 - x0, y1 - y0
    out = {"target": target, "width": round(w), "height": round(h), "aspect": round(w / h, 2) if h else 0,
           "components": comps, "flows": flows, "words": words,
           "fill": round(comp_area / (w * h), 2) if w and h else 0, "min_px": min_px or 11}
    advice, verdict = [], "ok"
    area = cfg.get("area_px")
    if area:
        scale = min(area[0] / w, area[1] / h, 1.0)
        out["scale"] = round(scale, 2)
        out["min_pt"] = round(out["min_px"] * scale * PX_TO_PT, 1)
        good, low = cfg.get("good_pt", 10), cfg.get("min_pt", 8)
        if out["min_pt"] < low:
            verdict = "too small"
            advice.append(f"Smallest text would be {out['min_pt']} pt on the {cfg.get('label', target)}; "
                          f"aim for {good} pt or more. Keep every component: shorten names and detail lines, "
                          f"merge layer rows holding one box, then tell the user the size and ask (one slide at "
                          f"this size, full-slide diagram, or zoomed-in slides in addition to the complete one).")
        elif out["min_pt"] < good:
            verdict = "tight"
            advice.append(f"Smallest text would be {out['min_pt']} pt; readable up close, hard from the back of a room "
                          f"(aim for {good} pt).")
        target_aspect = area[0] / area[1]
        if out["aspect"] < target_aspect / 1.6:
            advice.append(f"The diagram is tall and narrow ({out['aspect']}:1) for a {target_aspect:.1f}:1 area, "
                          f"so a lot of the area stays blank. Try direction LR, or fewer layers.")
        elif out["aspect"] > target_aspect * 1.6:
            advice.append(f"The diagram is very wide ({out['aspect']}:1) for a {target_aspect:.1f}:1 area. "
                          f"Try direction TB, or fewer boxes per layer.")
        for key, n, what in (("max_components", comps, "boxes"), ("max_flows", flows, "arrows"),
                             ("max_words", words, "words")):
            lim = cfg.get(key)
            if lim and n > lim:
                advice.append(f"{n} {what}; past about {lim} text gets small on a {cfg.get('label', target)}. "
                              f"Don't remove any to fit; use short wording and let the layout search work.")
    if out["fill"] < 0.12 and comps >= 4:
        advice.append(f"Only {int(out['fill'] * 100)}% of the canvas is boxes; the rest is gaps and arrow channels. "
                      f"Long arrows that skip layers and near-empty layers cause most of it.")
    out["verdict"], out["advice"] = verdict, advice
    return out


def score(m):
    """Higher is better: biggest smallest-text on the target, then fewer words, then compactness."""
    return (m.get("min_pt", 0), m.get("fill", 0))


def report_lines(m, cfg=None):
    if m.get("empty"):
        return ["Readability: nothing to measure"]
    lines = [f"Readability ({(cfg or {}).get('label', m['target'])}): {m['width']} x {m['height']} px, "
             f"{m['components']} boxes, {m['flows']} arrows, {m['words']} words, {int(m['fill'] * 100)}% filled"]
    if "min_pt" in m:
        lines.append(f"  fits the target at {int(m['scale'] * 100)}% -> smallest text {m['min_pt']} pt: {m['verdict']}")
    lines += [f"  advice: {a}" for a in m["advice"]]
    return lines
