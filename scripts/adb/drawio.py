"""Read, write, and query draw.io (.drawio) files.

Handles compressed and uncompressed pages, <object>/<UserObject> wrappers,
style strings, absolute geometry of nested cells, and HTML labels.
"""
import base64
import html
import re
import zlib
import xml.etree.ElementTree as ET
from urllib.parse import unquote

WRAPPERS = ("UserObject", "object")


# ----------------------------------------------------------------- styles
def parse_style(style):
    """Return (names, props): bare style names (e.g. 'text', 'ellipse') and key=value pairs."""
    names, props = [], {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            props[k.strip()] = v.strip()
        else:
            names.append(part)
    return names, props


def format_style(names, props):
    parts = list(names) + [f"{k}={v}" for k, v in props.items() if v is not None]
    return ";".join(parts) + (";" if parts else "")


def style_get(style, key, default=None):
    return parse_style(style)[1].get(key, default)


def style_set(style, **updates):
    names, props = parse_style(style)
    for k, v in updates.items():
        if v is None:
            props.pop(k, None)
        else:
            props[k] = str(v)
    return format_style(names, props)


def shape_of(style):
    names, props = parse_style(style)
    if "shape" in props:
        return props["shape"]
    for n in names:
        if n in ("text", "image", "ellipse", "rhombus", "triangle", "swimlane", "label", "line", "group", "edgeLabel"):
            return n
    return "rect"


# ----------------------------------------------------------------- labels
def label_text(value):
    """Plain text from a (possibly HTML) label; <br>/<div> become newlines."""
    if not value:
        return ""
    t = re.sub(r"(?i)<br\s*/?>|</div>|</p>|</li>", "\n", value)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def esc(text):
    return html.escape(str(text), quote=False)


# ----------------------------------------------------------------- file I/O
def _inflate(text):
    raw = base64.b64decode(text.strip())
    return unquote(zlib.decompress(raw, -15).decode("utf-8"))


class Page:
    """One <diagram> page with convenience accessors over its cells."""

    def __init__(self, name, diagram_el, model_el):
        self.name = name
        self.diagram = diagram_el
        self.model = model_el
        self.root = model_el.find("root")
        if self.root is None:
            self.root = ET.SubElement(model_el, "root")
        self.reindex()

    # indexing ---------------------------------------------------------
    def reindex(self):
        self.items = []          # (id, element-in-root, mxCell)
        for el in list(self.root):
            if el.tag == "mxCell":
                self.items.append((el.get("id"), el, el))
            elif el.tag in WRAPPERS:
                inner = el.find("mxCell")
                if inner is not None:
                    self.items.append((el.get("id"), el, inner))
        self.by_id = {cid: (outer, cell) for cid, outer, cell in self.items if cid}
        self.children = {}
        for cid, _, cell in self.items:
            self.children.setdefault(cell.get("parent"), []).append(cid)

    def cell(self, cid):
        return self.by_id[cid][1] if cid in self.by_id else None

    def outer(self, cid):
        return self.by_id[cid][0] if cid in self.by_id else None

    def value(self, cid):
        outer, cell = self.by_id[cid]
        return outer.get("label", "") if outer is not cell else cell.get("value", "")

    def set_value(self, cid, text):
        outer, cell = self.by_id[cid]
        if outer is not cell:
            outer.set("label", text)
        else:
            cell.set("value", text)

    def style(self, cid):
        return self.cell(cid).get("style", "") or ""

    def set_style(self, cid, style):
        self.cell(cid).set("style", style)

    def is_vertex(self, cid):
        return self.cell(cid).get("vertex") == "1"

    def is_edge(self, cid):
        return self.cell(cid).get("edge") == "1"

    def layer_ids(self):
        """Ids of the root cell and layer cells (cells whose parent is the root cell)."""
        roots = [cid for cid, _, c in self.items if c.get("parent") is None]
        layers = set(roots)
        for r in roots:
            layers.update(self.children.get(r, []))
        return {cid for cid in layers if cid in self.by_id and not self.is_vertex(cid) and not self.is_edge(cid)}

    def default_layer(self):
        layers = [cid for cid in self.layer_ids() if self.cell(cid).get("parent") is not None]
        return layers[0] if layers else "1"

    def descendants(self, cid):
        out, stack = [], list(self.children.get(cid, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.children.get(c, []))
        return out

    def ancestors(self, cid):
        out, cur, layers = [], self.cell(cid).get("parent"), self.layer_ids()
        while cur and cur in self.by_id and cur not in layers:
            out.append(cur)
            cur = self.cell(cur).get("parent")
        return out

    # geometry ---------------------------------------------------------
    def geometry(self, cid):
        return self.cell(cid).find("mxGeometry")

    def rel_rect(self, cid):
        g = self.geometry(cid)
        if g is None or g.get("relative") == "1":
            return None
        try:
            return [float(g.get("x", 0)), float(g.get("y", 0)), float(g.get("width", 0)), float(g.get("height", 0))]
        except ValueError:
            return None

    def abs_rect(self, cid):
        r = self.rel_rect(cid)
        if r is None:
            return None
        x, y, w, h = r
        for anc in self.ancestors(cid):
            if not self.is_vertex(anc):
                break
            pr = self.rel_rect(anc)
            if pr is None:
                break
            x, y = x + pr[0], y + pr[1]
        return [x, y, w, h]

    def abs_origin(self, cid):
        """Absolute top-left of a container cell (0,0 for layers)."""
        if cid is None or cid not in self.by_id or not self.is_vertex(cid):
            return 0.0, 0.0
        r = self.abs_rect(cid)
        return (r[0], r[1]) if r else (0.0, 0.0)

    def edge_points(self, cid):
        g = self.geometry(cid)
        pts = []
        if g is not None:
            arr = g.find("Array")
            if arr is not None and arr.get("as") == "points":
                for p in arr.findall("mxPoint"):
                    pts.append((float(p.get("x", 0)), float(p.get("y", 0))))
        return pts

    def new_id(self, base):
        base = re.sub(r"[^A-Za-z0-9_\-]", "_", base or "cell")[:40] or "cell"
        if base not in self.by_id:
            return base
        i = 2
        while f"{base}_{i}" in self.by_id:
            i += 1
        return f"{base}_{i}"


def load(path):
    """Return (tree, [Page]). Compressed pages are decoded in place."""
    tree = ET.parse(path)
    top = tree.getroot()
    pages = []
    if top.tag == "mxGraphModel":
        pages.append(Page("Page-1", None, top))
        return tree, pages
    if top.tag != "mxfile":
        raise ValueError(f"root element is <{top.tag}>, expected <mxfile>")
    for i, d in enumerate(top.findall("diagram"), start=1):
        model = d.find("mxGraphModel")
        if model is None:
            text = (d.text or "").strip()
            if not text:
                raise ValueError(f"page {i} is empty")
            model = ET.fromstring(_inflate(text))
            d.text = None
            d.append(model)
        pages.append(Page(d.get("name") or f"Page-{i}", d, model))
    if not pages:
        raise ValueError("no <diagram> pages found")
    return tree, pages


def pick_page(pages, name=None):
    if name is None:
        return pages[0]
    for p in pages:
        if p.name == name:
            return p
    if str(name).isdigit() and 0 < int(name) <= len(pages):
        return pages[int(name) - 1]
    raise ValueError(f"no page named {name!r}; pages: {[p.name for p in pages]}")


def save(tree, path):
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=False)


# ----------------------------------------------------------------- builders
def new_file(page_name="Page-1", page_id="page-1"):
    mxfile = ET.Element("mxfile", {"host": "app.diagrams.net", "agent": "architecture-diagram-builder", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": page_id, "name": page_name})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "dx": "1400", "dy": "900", "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1",
        "connect": "1", "arrows": "1", "fold": "1", "page": "1", "pageScale": "1",
        "pageWidth": "1169", "pageHeight": "827", "math": "0", "shadow": "0"})
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    return ET.ElementTree(mxfile), Page(page_name, diagram, model)


def make_vertex(cid, value, style, parent, x, y, w, h):
    cell = ET.Element("mxCell", {"id": cid, "value": value or "", "style": style, "vertex": "1", "parent": parent})
    ET.SubElement(cell, "mxGeometry", {"x": _num(x), "y": _num(y), "width": _num(w), "height": _num(h), "as": "geometry"})
    return cell


def make_edge(cid, value, style, parent, source, target, points=(), label_pos=None):
    cell = ET.Element("mxCell", {"id": cid, "value": value or "", "style": style, "edge": "1",
                                 "parent": parent, "source": source, "target": target})
    g = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    if label_pos is not None and abs(round(label_pos, 3)) >= 0.001:
        g.set("x", _num(round(label_pos, 3)))
    if points:
        arr = ET.SubElement(g, "Array", {"as": "points"})
        for px, py in points:
            ET.SubElement(arr, "mxPoint", {"x": _num(px), "y": _num(py)})
    return cell


def _num(v):
    v = float(v)
    return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:.2f}".rstrip("0").rstrip(".")
