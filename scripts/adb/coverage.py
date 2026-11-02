"""Completeness: does a new diagram still show every component of the one it was derived from?

Readability work (a slide version, a simpler redraw) must never lose architecture. This compares
the components of the original page with the new page by label, so a missing Object Store or a
component folded into a "Data stores" box is reported before anyone sees the new version.
"""
import re

from . import drawio as D


def _norm(text):
    t = re.sub(r"\(.*?\)", " ", text.lower())
    t = re.sub(r"[^a-z0-9×]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def components(page):
    """{id: name} for the components of a page: shapes the builder marked as components, or,
    in a hand-made diagram, boxes that carry text (not containers, text blocks or tiny badges)."""
    out = {}
    for cid, _, cell in page.items:
        if cell.get("vertex") != "1" or cid not in page.by_id:
            continue
        style = cell.get("style", "") or ""
        st = D.parse_style(style)[1]
        role = st.get("archRole")
        name = D.label_text(page.value(cid)).split("\n")[0].strip()
        if role == "component":
            out[cid] = name
        elif not role and name and st.get("container") != "1" and not style.startswith("text"):
            g = page.geometry(cid)
            if g is not None and float(g.get("width", 0)) >= 60 and float(g.get("height", 0)) >= 24:
                out[cid] = name
    return out


def _suffix(cid):
    return cid.split("_", 1)[1] if "_" in cid else cid


def compare(old_page, new_page, ignore=()):
    """Components of `old_page` with no counterpart on `new_page`, as [(id, name)].
    A counterpart has the same id, the same id after its prefix (v3_minio ~ s_minio),
    or a name that equals or contains the other's (at least 4 characters)."""
    old, new = components(old_page), components(new_page)
    new_ids = set(new) | {_suffix(i) for i in new}
    new_names = [_norm(n) for n in new.values()]
    missing, ignored = [], 0
    for cid, name in old.items():
        if cid in ignore or _suffix(cid) in ignore or name in ignore:
            ignored += 1
            continue
        if cid in new_ids or _suffix(cid) in new_ids:
            continue
        n = _norm(name)
        if n and any(n == m or (len(n) >= 4 and n in m) or (len(m) >= 4 and m in n) for m in new_names):
            continue
        missing.append((cid, name))
    return missing, len(old) - ignored, ignored
