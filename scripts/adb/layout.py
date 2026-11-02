"""Layered layout and orthogonal routing for architecture diagrams.

Model: layers are columns (LR) or rows (TB) along the primary axis. Boundaries
(cloud, cluster, on-prem ...) are nested blocks stacked along the cross axis;
a block's items (nodes and child blocks) are packed into tracks, so a boundary
rectangle can only ever contain its own members. Edges are routed through the
empty gaps between layers (lanes) and between tracks (channels), so they never
pass through boxes. Everything is computed in abstract (primary p, cross c)
coordinates and mapped to x/y at the end.
"""
import itertools
import math

ROOT = "__root__"


def text_width(text, size=11, bold=False):
    w = 0.0
    for ch in str(text):
        if ch in "il.,:;|!'`":
            w += 0.28
        elif ch in "mwMW@%":
            w += 0.86
        elif ch == " ":
            w += 0.28
        elif ch.isupper() or ch.isdigit():
            w += 0.64
        else:
            w += 0.53
    return w * size * (1.07 if bold else 1.0)


class Node:
    def __init__(self, nid, spec):
        self.id = nid
        self.layer = spec["layer"]
        self.boundary = spec.get("boundary") or ROOT
        self.order = spec.get("order", 0)
        self.bw, self.bh = spec["box"]                # physical box (includes node-mode label)
        self.cdx, self.cdy, self.cw, self.ch = spec["cell"]   # draw.io cell inside the box
        self.p = self.c = 0.0


class Block:
    def __init__(self, bid, parent, spec):
        self.id = bid
        self.parent = parent
        self.spec = spec or {}
        self.children, self.nodes = [], []
        self.col_lo = self.col_hi = None
        self.tracks, self.track_h, self.track_align = [], [], []
        self.size_c = self.c0 = self.p0 = self.p1 = 0.0
        self.header_h = 0.0


class Layout:
    def __init__(self, nodes, boundaries, edges, n_layers, opts):
        self.o = dict(opts)
        self.lr = self.o.get("direction", "LR") == "LR"
        self.nodes = {k: Node(k, v) for k, v in nodes.items()}
        self.edges = [dict(e) for e in edges if e["src"] != e["dst"]]
        self.n_layers = n_layers
        self.warnings = []
        self.blocks = {ROOT: Block(ROOT, None, {})}
        for bid, b in sorted(boundaries.items(), key=lambda kv: kv[1].get("order", 0)):
            self.blocks[bid] = Block(bid, b.get("parent") or ROOT, b)
        for bid, b in self.blocks.items():
            if bid != ROOT:
                self.blocks[b.parent].children.append(bid)
        for n in sorted(self.nodes.values(), key=lambda n: n.order):
            self.blocks[n.boundary].nodes.append(n.id)
        self._ranges(ROOT)
        empty = [b for b, blk in self.blocks.items() if b != ROOT and blk.col_lo is None]
        for b in empty:
            self.warnings.append(f"boundary '{b}' has no components and was not drawn")
            parent = self.blocks[self.blocks[b].parent]
            parent.children.remove(b)
            del self.blocks[b]
        for blk in self.blocks.values():
            blk.header_h = self.o["header"] if blk.id != ROOT else 0.0
        self.neighbors = {n: [] for n in self.nodes}
        for e in self.edges:
            self.neighbors[e["src"]].append(e["dst"])
            self.neighbors[e["dst"]].append(e["src"])

    # ------------------------------------------------------------ structure
    def _ranges(self, bid):
        blk = self.blocks[bid]
        cols = [self.nodes[n].layer for n in blk.nodes]
        for ch in blk.children:
            self._ranges(ch)
            cb = self.blocks[ch]
            if cb.col_lo is not None:
                cols += [cb.col_lo, cb.col_hi]
        blk.col_lo, blk.col_hi = (min(cols), max(cols)) if cols else (None, None)

    def pads(self, blk):
        if blk.id == ROOT:
            return 0.0, 0.0, 0.0, 0.0
        pad = self.o["pad"]
        if self.lr:
            return pad, pad, blk.header_h + pad, pad          # ps, pe, cs, ce
        return blk.header_h + pad, pad, pad, pad

    def start_ext(self, bid):
        blk = self.blocks[bid]
        inner = max([self.start_ext(c) for c in blk.children if self.blocks[c].col_lo == blk.col_lo] or [0.0])
        return self.pads(blk)[0] + inner

    def end_ext(self, bid):
        blk = self.blocks[bid]
        inner = max([self.end_ext(c) for c in blk.children if self.blocks[c].col_hi == blk.col_hi] or [0.0])
        return self.pads(blk)[1] + inner

    def sp(self, n):
        return n.bw if self.lr else n.bh

    def sc(self, n):
        return n.bh if self.lr else n.bw

    def cell_abs(self, n):
        """Cell rect in abstract coords: (p, c, size_p, size_c)."""
        if self.lr:
            return n.p + n.cdx, n.c + n.cdy, n.cw, n.ch
        return n.p + n.cdy, n.c + n.cdx, n.ch, n.cw

    # ------------------------------------------------------------ packing (cross axis)
    # A block's items are packed into tracks (bands along the cross axis). In a track,
    # a multi-column child block owns its columns; single-column items (nodes and
    # one-column blocks) form an ordered stack per column. Stacks keep key order,
    # so the vertical order inside each column follows the barycenter keys.
    def item_range(self, it):
        if it[0] == "n":
            lay = self.nodes[it[1]].layer
            return lay, lay
        b = self.blocks[it[1]]
        return b.col_lo, b.col_hi

    def item_h(self, it):
        return self.sc(self.nodes[it[1]]) if it[0] == "n" else self.blocks[it[1]].size_c

    def stack_h(self, stack):
        g = self.o["row_gap"]
        return sum(self.item_h(it) for it in stack) + g * max(0, len(stack) - 1)

    def pack(self, bid, key):
        blk = self.blocks[bid]
        for ch in blk.children:
            self.pack(ch, key)
            self.size(ch)
        items = [("n", n) for n in blk.nodes] + [("b", b) for b in blk.children]
        items.sort(key=lambda it: (key[it], it[1]))
        g = self.o["row_gap"]
        tracks, last = [], {}
        for it in items:
            lo, hi = self.item_range(it)
            h = self.item_h(it)
            k = key[it]
            best, best_cost = None, None
            if lo == hi:
                c = lo
                min_t = last.get(c, 0)
                if c in last and c in tracks[last[c]]["multi_cols"]:
                    min_t = last[c] + 1
                for t in range(min_t, len(tracks)):
                    tr = tracks[t]
                    if c in tr["multi_cols"]:
                        continue
                    st = tr["stacks"].get(c, [])
                    new_h = self.stack_h(st) + (g if st else 0) + h
                    cost = abs(tr["sum"] / tr["n"] - k) + 2 * max(0.0, new_h - tr["band"])
                    if best_cost is None or cost < best_cost:
                        best, best_cost = t, cost
            else:
                cols = range(lo, hi + 1)
                min_t = max([last[c] for c in cols if c in last] or [-1]) + 1
                for t in range(min_t, len(tracks)):
                    tr = tracks[t]
                    if any(c in tr["multi_cols"] or tr["stacks"].get(c) for c in cols):
                        continue
                    cost = abs(tr["sum"] / tr["n"] - k) + 2 * max(0.0, h - tr["band"])
                    if best_cost is None or cost < best_cost:
                        best, best_cost = t, cost
            if best is None:
                tracks.append({"multi": [], "multi_cols": set(), "stacks": {}, "sum": 0.0, "n": 0, "band": 0.0})
                best = len(tracks) - 1
            tr = tracks[best]
            if lo == hi:
                tr["stacks"].setdefault(lo, []).append(it)
                tr["band"] = max(tr["band"], self.stack_h(tr["stacks"][lo]))
            else:
                tr["multi"].append(it[1])
                tr["multi_cols"].update(range(lo, hi + 1))
                tr["band"] = max(tr["band"], h)
            tr["sum"] += k
            tr["n"] += 1
            for c in range(lo, hi + 1):
                last[c] = best
        blk.tracks = tracks

    def size(self, bid):
        blk = self.blocks[bid]
        for ch in blk.children:
            self.size(ch)
        for tr in blk.tracks:
            band = 0.0
            for b in tr["multi"]:
                band = max(band, self.blocks[b].size_c)
            for st in tr["stacks"].values():
                band = max(band, self.stack_h(st))
            tr["band"] = band
        ps, pe, cs, ce = self.pads(blk)
        blk.size_c = cs + sum(tr["band"] for tr in blk.tracks) + self.o["row_gap"] * max(0, len(blk.tracks) - 1) + ce

    def place_c(self, bid, c0):
        blk = self.blocks[bid]
        shift = c0 - blk.c0
        blk.c0 = c0
        c = c0 + self.pads(blk)[2]
        for tr in blk.tracks:
            tr["top"] = c
            for b in tr["multi"]:
                self.place_c(b, c)
            for st in tr["stacks"].values():
                y = c
                for it in st:
                    if it[0] == "n":
                        self.nodes[it[1]].c = y
                    else:
                        self.place_c(it[1], y)
                    y += self.item_h(it) + self.o["row_gap"]
            c += tr["band"] + self.o["row_gap"]

    def _shift_block(self, bid, new_c0):
        blk = self.blocks[bid]
        d = new_c0 - blk.c0
        if abs(d) < 1e-6:
            return
        blk.c0 = new_c0
        for n in blk.nodes:
            self.nodes[n].c += d
        for tr in blk.tracks:
            tr["top"] = tr.get("top", 0.0) + d
        for ch in blk.children:
            self._shift_block(ch, self.blocks[ch].c0 + d)

    def _desired_top(self, it):
        """Where an item would like its top edge, judging by its neighbours outside it."""
        if it[0] == "n":
            n = self.nodes[it[1]]
            nb = self.neighbors[it[1]]
            if not nb:
                return None
            return sum(self.nodes[m].c + self.sc(self.nodes[m]) / 2 for m in nb) / len(nb) - self.sc(n) / 2
        members = self._members(it[1])
        shifts = []
        for m in members:
            mc = self.nodes[m].c + self.sc(self.nodes[m]) / 2
            for o in self.neighbors[m]:
                if o not in members:
                    shifts.append(self.nodes[o].c + self.sc(self.nodes[o]) / 2 - mc)
        if not shifts:
            return None
        return self.blocks[it[1]].c0 + sum(shifts) / len(shifts)

    def _item_top(self, it):
        return self.nodes[it[1]].c if it[0] == "n" else self.blocks[it[1]].c0

    def _set_item_top(self, it, top):
        if it[0] == "n":
            self.nodes[it[1]].c = top
        else:
            self._shift_block(it[1], top)

    def straighten(self, passes=4):
        """Slide items within their track band towards their neighbours, keeping order and gaps."""
        grid, g = self.o.get("grid", 10), self.o["row_gap"]
        for _ in range(passes):
            for bid in self._topdown():
                blk = self.blocks[bid]
                for tr in blk.tracks:
                    top, band = tr["top"], tr["band"]
                    for b in tr["multi"]:
                        want = self._desired_top(("b", b))
                        if want is None:
                            continue
                        hi = top + band - self.blocks[b].size_c
                        self._shift_block(b, _snap(min(max(want, top), max(top, hi)), grid, top, max(top, hi)))
                    for st in tr["stacks"].values():
                        hs = [self.item_h(it) for it in st]
                        offs, acc = [], 0.0
                        for h in hs:
                            offs.append(acc)
                            acc += h + g
                        total = acc - g
                        wants = []
                        for it, o in zip(st, offs):
                            w = self._desired_top(it)
                            wants.append((self._item_top(it) if w is None else w) - o)
                        u = _isotonic(wants)
                        lo, hi = top, max(top, top + band - total)
                        for it, o, uu in zip(st, offs, u):
                            self._set_item_top(it, _snap(min(max(uu, lo), hi), grid, lo, hi) + o)

    def _topdown(self):
        out, stack = [], [ROOT]
        while stack:
            b = stack.pop(0)
            out.append(b)
            stack.extend(self.blocks[b].children)
        return out

    def _members(self, bid):
        blk = self.blocks[bid]
        out = set(blk.nodes)
        for ch in blk.children:
            out |= self._members(ch)
        return out

    # ------------------------------------------------------------ primary axis
    def place_p(self, gaps):
        L = self.n_layers
        self.col_w = [0.0] * L
        for n in self.nodes.values():
            self.col_w[n.layer] = max(self.col_w[n.layer], self.sp(n))
        lead = max([self.start_ext(b) for b, blk in self.blocks.items() if b != ROOT and blk.col_lo == 0] or [0.0])
        self.col_start = []
        p = lead
        for k in range(L):
            self.col_start.append(p)
            p += self.col_w[k] + (gaps[k] if k < L - 1 else 0.0)
        for n in self.nodes.values():
            n.p = self.col_start[n.layer] + (self.col_w[n.layer] - self.sp(n)) / 2
        self._block_p(ROOT)

    def _block_p(self, bid):
        blk = self.blocks[bid]
        lo, hi = [], []
        for n in blk.nodes:
            nd = self.nodes[n]
            lo.append(nd.p)
            hi.append(nd.p + self.sp(nd))
        for ch in blk.children:
            self._block_p(ch)
            lo.append(self.blocks[ch].p0)
            hi.append(self.blocks[ch].p1)
        ps, pe, _, _ = self.pads(blk)
        blk.p0, blk.p1 = (min(lo) - ps, max(hi) + pe) if lo else (0.0, 0.0)

    def col_end(self, k):
        return self.col_start[k] + self.col_w[k]

    def gap_region(self, k):
        """Free primary interval for lanes in the gap after column k."""
        m = self.o["lane"] * 0.75
        end_ext = max([self.end_ext(b) for b, blk in self.blocks.items() if b != ROOT and blk.col_hi == k] or [0.0])
        lo = self.col_end(k) + end_ext + m
        if k + 1 < self.n_layers:
            start_ext = max([self.start_ext(b) for b, blk in self.blocks.items() if b != ROOT and blk.col_lo == k + 1] or [0.0])
            hi = self.col_start[k + 1] - start_ext - m
        else:
            hi = lo + self.o["lane"] * (self.trailing_lanes + 1)
        return lo, max(hi, lo + 1)

    # ------------------------------------------------------------ obstacles
    def header_rects(self):
        out = []
        for bid, blk in self.blocks.items():
            if bid == ROOT:
                continue
            tw = blk.spec.get("label_w", 80) + (28 if blk.spec.get("icon") else 0) + 16
            if self.lr:
                out.append((blk.p0 + 4, blk.c0, min(tw, blk.p1 - blk.p0 - 8), blk.header_h))
            else:
                out.append((blk.p0, blk.c0 + 4, blk.header_h, min(tw, blk.size_c - 8)))
        return out

    def box_abs(self, n):
        return n.p, n.c, self.sp(n), self.sc(n)

    # ------------------------------------------------------------ routing
    def classify(self):
        """Decide each edge's route type and which gaps its lanes use."""
        lanes = {}
        label_need = {}
        cols = {}
        for n in self.nodes.values():
            cols.setdefault(n.layer, []).append(n)
        headers = self.header_rects_c()
        for e in self.edges:
            u, v = self.nodes[e["src"]], self.nodes[e["dst"]]
            cu, cv = u.layer, v.layer
            if cu < cv:
                e["kind_r"] = "adj" if cv == cu + 1 else "long"
                e["dir"] = "fwd"
                e["gaps"] = [cu] if cv == cu + 1 else [cu, cv - 1]
            elif cu > cv:
                e["kind_r"] = "adj" if cu == cv + 1 else "long"
                e["dir"] = "bwd"
                e["gaps"] = [cv] if cu == cv + 1 else [cu - 1, cv]
            else:
                e["dir"] = "same"
                lo_n, hi_n = (u, v) if u.c < v.c else (v, u)
                a, b = lo_n.c + self.sc(lo_n), hi_n.c
                between = any(w is not u and w is not v and w.c < b and w.c + self.sc(w) > a for w in cols[cu])
                blocked = between or any(a < hc + hh and hc < b and hlo <= cu <= hhi for hc, hh, hlo, hhi in headers)
                if blocked or b - a < 16:
                    e["kind_r"] = "side"
                    e["gaps"] = [cu]
                else:
                    e["kind_r"] = "z"
                    e["gaps"] = []
            for g in e["gaps"]:
                lanes[g] = lanes.get(g, 0) + 1
            if e.get("label_box") and e["kind_r"] in ("adj", "side"):
                w, h = e["label_box"]
                need = w if self.lr else h
                label_need[e["gaps"][0]] = max(label_need.get(e["gaps"][0], 0.0), need)
        self.trailing_lanes = lanes.get(self.n_layers - 1, 0)
        return lanes, label_need

    def header_rects_c(self):
        """Cross-axis header bands (LR only): (c, h, col_lo, col_hi)."""
        if not self.lr:
            return []
        return [(b.c0, b.header_h, b.col_lo, b.col_hi) for bid, b in self.blocks.items() if bid != ROOT]

    def compute_gaps(self, lanes, label_need):
        L = self.n_layers
        gaps = []
        lm = self.o["label_margin"]
        for k in range(L - 1):
            end_ext = max([self.end_ext(b) for b, blk in self.blocks.items() if b != ROOT and blk.col_hi == k] or [0.0])
            start_ext = max([self.start_ext(b) for b, blk in self.blocks.items() if b != ROOT and blk.col_lo == k + 1] or [0.0])
            inner = max(lanes.get(k, 0) * self.o["lane"] + self.o["lane"], label_need.get(k, 0.0) + 2 * lm)
            g = max(self.o["layer_gap"], end_ext + start_ext + inner + self.o["lane"] * 1.5)
            gaps.append(math.ceil(g / self.o.get("grid", 10)) * self.o.get("grid", 10))
        return gaps

    def route(self):
        o = self.o
        boxes = {n.id: self.box_abs(n) for n in self.nodes.values()}
        hdrs = self.header_rects()
        # ---- channels for long edges
        for e in self.edges:
            if e["kind_r"] != "long":
                continue
            u, v = self.nodes[e["src"]], self.nodes[e["dst"]]
            cu_c = u.c + self.sc(u) / 2
            cv_c = v.c + self.sc(v) / 2
            if e["dir"] == "fwd":
                s0, s1 = self.col_end(u.layer), self.col_start[v.layer]
            else:
                s0, s1 = self.col_end(v.layer), self.col_start[u.layer]
            obst = []
            for nid, (bp, bc, bsp, bsc) in boxes.items():
                if nid in (u.id, v.id):
                    continue
                if bp < s1 and bp + bsp > s0:
                    obst.append((bc, bc + bsc))
            for hp, hc, hsp, hsc in hdrs:
                if hp < s1 and hp + hsp > s0:
                    obst.append((hc, hc + hsc))
            for bid, blk in self.blocks.items():
                if bid != ROOT and blk.p0 < s1 and blk.p1 > s0:
                    # never run a channel along a boundary border: it would blur what is inside
                    obst.append((blk.c0 - 2, blk.c0 + 2))
                    obst.append((blk.c0 + blk.size_c - 2, blk.c0 + blk.size_c + 2))
            e["free"] = self._free_intervals(obst, o["lane"])
            e["pref_c"] = (cu_c + cv_c) / 2
            e["span"] = (s0, s1)
        self._assign_channels()
        # ---- ports
        sides = {}
        for e in self.edges:
            u, v = self.nodes[e["src"]], self.nodes[e["dst"]]
            kr, d = e["kind_r"], e["dir"]
            if kr in ("adj", "long"):
                su, sv = ("pe", "ps") if d == "fwd" else ("ps", "pe")
                ku = e["chan"] if kr == "long" else v.c + self.sc(v) / 2
                kv = e["chan"] if kr == "long" else u.c + self.sc(u) / 2
            elif kr == "side":
                su = sv = "pe"
                ku, kv = v.c + self.sc(v) / 2, u.c + self.sc(u) / 2
            else:  # z
                if u.c < v.c:
                    su, sv = "ce", "cs"
                else:
                    su, sv = "cs", "ce"
                ku, kv = v.p + self.sp(v) / 2, u.p + self.sp(u) / 2
            e["su"], e["sv"] = su, sv
            sides.setdefault((u.id, su), []).append((ku, e["id"], "u"))
            sides.setdefault((v.id, sv), []).append((kv, e["id"], "v"))
        by_id = {e["id"]: e for e in self.edges}
        bottom_side = "ce" if self.lr else "pe"
        for (nid, side), lst in sides.items():
            lst.sort()
            n = self.nodes[nid]
            cp, cc, csp, csc = self.cell_abs(n)
            # icon-first nodes carry their label below the icon: connect bottom-side ports
            # under the label (draw.io exitDy/entryDy) so connectors never cut through text
            drop = 0.0
            if side == bottom_side and (n.cw, n.ch) != (n.bw, n.bh):
                drop = (n.bh - (n.cdy + n.ch))
            for i, (_, eid, role) in enumerate(lst):
                f = (i + 1) / (len(lst) + 1)
                if side in ("pe", "ps"):
                    pt = (cp + csp + (drop if side == "pe" else 0) if side == "pe" else cp, cc + f * csc)
                else:
                    pt = (cp + f * csp, cc + csc + drop if side == "ce" else cc)
                by_id[eid]["port_" + role] = pt
                by_id[eid]["frac_" + role] = f
                by_id[eid]["drop_" + role] = drop
        # snap a channel onto its port when the port lies inside the same free interval (removes small jogs),
        # unless that would put it on top of another long connector's channel
        for e in self.edges:
            if e["kind_r"] != "long" or not e.get("chan_iv"):
                continue
            a, b = e["chan_iv"]
            for port in (e["port_u"][1], e["port_v"][1]):
                if a + 4 < port < b - 4 and abs(port - e["chan"]) < 24 and self._chan_free(e, port):
                    e["chan"] = port
                    break
        # ---- lanes in primary gaps
        segs = {}
        for e in self.edges:
            pu, pv = e["port_u"], e["port_v"]
            kr, d = e["kind_r"], e["dir"]
            if kr == "adj":
                g = e["gaps"][0]
                stubs = [("s", pu[1]), ("e", pv[1])] if d == "fwd" else [("e", pu[1]), ("s", pv[1])]
                segs.setdefault(g, []).append((e["id"], 0, pu[1], pv[1], stubs))
            elif kr == "long":
                g1, g2 = e["gaps"]
                if d == "fwd":
                    segs.setdefault(g1, []).append((e["id"], 0, pu[1], e["chan"], [("s", pu[1]), ("e", e["chan"])]))
                    segs.setdefault(g2, []).append((e["id"], 1, e["chan"], pv[1], [("s", e["chan"]), ("e", pv[1])]))
                else:
                    segs.setdefault(g1, []).append((e["id"], 0, pu[1], e["chan"], [("e", pu[1]), ("s", e["chan"])]))
                    segs.setdefault(g2, []).append((e["id"], 1, e["chan"], pv[1], [("e", e["chan"]), ("s", pv[1])]))
            elif kr == "side":
                segs.setdefault(e["gaps"][0], []).append((e["id"], 0, pu[1], pv[1], [("s", pu[1]), ("s", pv[1])]))
        lane_x = {}
        for g, lst in segs.items():
            order = self._lane_order(lst)
            lo, hi = self.gap_region(g)
            n = len(order)
            step = min(o["lane"], (hi - lo) / (n + 1))
            mid = (lo + hi) / 2
            for i, s in enumerate(order):
                lane_x[(s[0], s[1])] = mid + (i - (n - 1) / 2) * step
        # ---- bends
        for e in self.edges:
            pu, pv = e["port_u"], e["port_v"]
            kr = e["kind_r"]
            if kr in ("adj", "side"):
                x = lane_x[(e["id"], 0)]
                bends = [] if (kr == "adj" and abs(pu[1] - pv[1]) < 0.5) else [(x, pu[1]), (x, pv[1])]
            elif kr == "long":
                x1, x2, c = lane_x[(e["id"], 0)], lane_x[(e["id"], 1)], e["chan"]
                bends = [(x1, pu[1]), (x1, c), (x2, c), (x2, pv[1])]
                if abs(c - pu[1]) < 0.5:
                    bends = bends[2:]
                if abs(c - pv[1]) < 0.5:
                    bends = bends[:-2] if len(bends) == 4 else []
            else:
                lo_n, hi_n = sorted([self.nodes[e["src"]], self.nodes[e["dst"]]], key=lambda n: n.c)
                mid = (lo_n.c + self.sc(lo_n) + hi_n.c) / 2
                bends = [] if abs(pu[0] - pv[0]) < 0.5 else [(pu[0], mid), (pv[0], mid)]
            e["path_a"] = [pu] + bends + [pv]
            e["bends_a"] = bends

    def _free_intervals(self, obst, margin):
        obst = sorted((a - margin, b + margin) for a, b in obst)
        free, cur = [], -math.inf
        for a, b in obst:
            if a > cur:
                free.append((cur, a))
            cur = max(cur, b)
        free.append((cur, math.inf))
        return [(a, b) for a, b in free if b - a > 2 * margin or math.isinf(a) or math.isinf(b)]

    def _assign_channels(self):
        lane = self.o["lane"]
        groups = {}
        for e in self.edges:
            if e["kind_r"] != "long":
                continue
            u, v = self.nodes[e["src"]], self.nodes[e["dst"]]
            yu, yv = u.c + self.sc(u) / 2, v.c + self.sc(v) / 2
            best = None
            for a, b in e["free"]:
                for want in (yu, yv, e["pref_c"]):
                    cand = min(max(want, a + lane), b - lane)
                    legs = (abs(cand - yu) > 1) + (abs(cand - yv) > 1)
                    cost = abs(cand - yu) + abs(cand - yv) + 40 * legs
                    if best is None or cost < best[0] - 1e-6:
                        best = (cost, (a, b), cand)
            e["chan_iv"], e["chan"] = best[1], best[2]
            groups.setdefault(best[1], []).append(e)
        for (a, b), lst in groups.items():
            lst.sort(key=lambda e: e["chan"])
            n = len(lst)
            if not math.isinf(a) and not math.isinf(b):
                step = (b - a) / (n + 1)
                if step < lane * 0.7:
                    self.warnings.append("several long connectors share a narrow channel; consider fewer cross-layer flows")
                for i, e in enumerate(lst):
                    e["chan"] = a + step * (i + 1)
            elif math.isinf(b) and not math.isinf(a):
                for i, e in enumerate(lst):
                    e["chan"] = a + lane * (i + 1)
            elif math.isinf(a) and not math.isinf(b):
                for i, e in enumerate(reversed(lst)):
                    e["chan"] = b - lane * (i + 1)
            else:
                prev = -math.inf
                for e in lst:
                    e["chan"] = max(e["chan"], prev + lane)
                    prev = e["chan"]
        self._separate_channels()

    def _spans_meet(self, e, f):
        return e["span"][0] < f["span"][1] and f["span"][0] < e["span"][1]

    def _chan_free(self, e, c):
        """No other long connector whose span overlaps e's runs within 3/4 of a lane of c."""
        lane = self.o["lane"]
        return all(abs(f["chan"] - c) >= lane * 0.75 for f in self.edges
                   if f is not e and f["kind_r"] == "long" and "chan" in f and self._spans_meet(e, f))

    def _separate_channels(self):
        """Channels were spread per free interval, but two connectors whose spans only partly overlap get
        different intervals and can land on the same line. Move the later one to the nearest free line."""
        lane = self.o["lane"]
        longs = [e for e in self.edges if e["kind_r"] == "long" and "chan" in e]
        for e in longs:
            if self._chan_free(e, e["chan"]):
                continue
            a, b = e["chan_iv"]
            lo = a + lane if not math.isinf(a) else e["chan"] - 20 * lane
            hi = b - lane if not math.isinf(b) else e["chan"] + 20 * lane
            for k in range(1, 41):
                for c in (e["chan"] + k * lane / 2, e["chan"] - k * lane / 2):
                    if lo <= c <= hi and self._chan_free(e, c):
                        e["chan"] = c
                        break
                else:
                    continue
                break

    def _lane_order(self, segs):
        def crossings(order):
            total = 0
            for i in range(len(order)):
                for j in range(i + 1, len(order)):
                    a, b = order[i], order[j]          # a nearer the start side than b
                    alo, ahi = sorted((a[2], a[3]))
                    blo, bhi = sorted((b[2], b[3]))
                    total += sum(1 for side, c in b[4] if side == "s" and alo + 0.5 < c < ahi - 0.5)
                    total += sum(1 for side, c in a[4] if side == "e" and blo + 0.5 < c < bhi - 0.5)
                    # a's end stub runs right past b's lane and b's start stub runs left past a's lane;
                    # at (almost) the same height they would lie on top of each other: worse than a crossing
                    total += sum(3 for sa, ca in a[4] for sb, cb in b[4]
                                 if sa == "e" and sb == "s" and abs(ca - cb) < 4)
            return total
        if len(segs) <= 1:
            return list(segs)
        if len(segs) <= 7:
            best = min(itertools.permutations(segs), key=lambda p: (crossings(p), [s[0] for s in p]))
            return list(best)
        order = sorted(segs, key=lambda s: (s[3] - s[2] > 0, s[2]))
        cost = crossings(order)
        improved = True
        while improved:
            improved = False
            # move any lane to any position (neighbour swaps alone get stuck)
            for i in range(len(order)):
                rest = order[:i] + order[i + 1:]
                for j in range(len(order)):
                    if j == i:
                        continue
                    cand = rest[:j] + [order[i]] + rest[j:]
                    c = crossings(cand)
                    if c < cost:
                        order, cost, improved = cand, c, True
                        break
                if improved:
                    break
        return order

    # ------------------------------------------------------------ evaluation
    def to_xy(self, p, c):
        return (p, c) if self.lr else (c, p)

    def evaluate(self):
        segs = []
        for e in self.edges:
            pts = [self.to_xy(*pt) for pt in e["path_a"]]
            e["path"] = pts
            for a, b in zip(pts, pts[1:]):
                segs.append((e["id"], a, b))
        crossings = overlaps = through = 0
        hor = [(i, s) for i, s in enumerate(segs) if abs(s[1][1] - s[2][1]) < 0.5 and abs(s[1][0] - s[2][0]) > 0.5]
        ver = [(i, s) for i, s in enumerate(segs) if abs(s[1][0] - s[2][0]) < 0.5 and abs(s[1][1] - s[2][1]) > 0.5]
        for _, h in hor:
            hx0, hx1 = sorted((h[1][0], h[2][0]))
            hy = h[1][1]
            for _, v in ver:
                if v[0] == h[0]:
                    continue
                vy0, vy1 = sorted((v[1][1], v[2][1]))
                vx = v[1][0]
                if hx0 + 0.5 < vx < hx1 - 0.5 and vy0 + 0.5 < hy < vy1 - 0.5:
                    crossings += 1
        for group in (hor, ver):
            axis = 1 if group is hor else 0
            other = 1 - axis
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i][1], group[j][1]
                    # lines 1-3 px apart look like one line, so they count as overlapping too
                    if a[0] == b[0] or abs(a[1][axis] - b[1][axis]) > 3.5:
                        continue
                    a0, a1 = sorted((a[1][other], a[2][other]))
                    b0, b1 = sorted((b[1][other], b[2][other]))
                    if min(a1, b1) - max(a0, b0) > 2:
                        overlaps += 1
        boxes = []
        for n in self.nodes.values():
            x, y = self.to_xy(n.p, n.c)
            w, h = (self.sp(n), self.sc(n)) if self.lr else (self.sc(n), self.sp(n))
            boxes.append((n.id, x, y, w, h))
        for eid, a, b in segs:
            e = next(x for x in self.edges if x["id"] == eid)
            for nid, x, y, w, h in boxes:
                if nid in (e["src"], e["dst"]):
                    continue
                if _seg_hits_rect(a, b, (x + 1, y + 1, w - 2, h - 2)):
                    through += 1
        bends = sum(len(e["bends_a"]) for e in self.edges)
        length = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for _, a, b in segs)
        # boundaries side by side should keep the spec's reading order (Offline | Common | Online);
        # each flipped pair costs as much as four crossings, so it only happens when it clearly helps
        flipped = 0
        for blk in self.blocks.values():
            kids = [self.blocks[c] for c in blk.children]       # children are in spec order
            for i, a in enumerate(kids):
                for b in kids[i + 1:]:
                    side_by_side = not (a.col_hi < b.col_lo or b.col_hi < a.col_lo)
                    if side_by_side and a.c0 > b.c0 + 0.5:
                        flipped += 1
        self.stats = {"crossings": crossings, "overlaps": overlaps, "through": through, "bends": bends,
                      "length": round(length)}
        area = (self.col_end(self.n_layers - 1) + 100) * (self.blocks[ROOT].size_c + 100)
        return (10 * crossings + 25 * overlaps + 200 * through + 0.5 * bends + 0.002 * length + 1e-5 * area
                + 40 * flipped)

    # ------------------------------------------------------------ driver
    def run_once(self, key):
        self.pack(ROOT, key)
        self.size(ROOT)
        self.place_c(ROOT, 0.0)
        self.straighten()
        lanes, label_need = self.classify()
        gaps = self.compute_gaps(lanes, label_need)
        self.place_p(gaps)
        if self._update_headers():
            self.pack(ROOT, key)
        self.size(ROOT)
        self.place_c(ROOT, 0.0)
        self.straighten()
        lanes, label_need = self.classify()
        gaps = self.compute_gaps(lanes, label_need)
        self.place_p(gaps)
        self.route()
        return self.evaluate()

    def _update_headers(self):
        changed = False
        for bid, blk in self.blocks.items():
            if bid == ROOT:
                continue
            avail = (blk.p1 - blk.p0) if self.lr else (blk.size_c)
            need = blk.spec.get("label_w", 80) + (28 if blk.spec.get("icon") else 0) + 24
            lines = max(1, math.ceil(need / max(avail, 60)))
            h = self.o["header"] + (lines - 1) * 20
            if abs(h - blk.header_h) > 0.5:
                blk.header_h, changed = h, True
        return changed

    def snapshot(self):
        return ({n: (nd.p, nd.c) for n, nd in self.nodes.items()},
                {b: (bl.p0, bl.p1, bl.c0, bl.size_c, bl.header_h, _copy_tracks(bl.tracks)) for b, bl in self.blocks.items()},
                [dict(e) for e in self.edges], dict(self.stats), list(self.col_start), list(self.col_w))

    def restore(self, snap):
        npos, bpos, edges, stats, cs, cw = snap
        for n, (p, c) in npos.items():
            self.nodes[n].p, self.nodes[n].c = p, c
        for b, (p0, p1, c0, sc, hh, tr) in bpos.items():
            bl = self.blocks[b]
            bl.p0, bl.p1, bl.c0, bl.size_c, bl.header_h, bl.tracks = p0, p1, c0, sc, hh, tr
        self.edges, self.stats, self.col_start, self.col_w = edges, stats, cs, cw

    def keys_from_order(self, reverse=False):
        step = self.o["row_gap"] + self.o.get("typical_c", 64)
        key = {}
        order = sorted(self.nodes.values(), key=lambda n: n.order, reverse=reverse)
        for i, n in enumerate(order):
            key[("n", n.id)] = i * step
        self._block_keys(key)
        return key

    def keys_from_barycenter(self):
        key = {}
        for n in self.nodes.values():
            nb = self.neighbors[n.id]
            own = n.c + self.sc(n) / 2
            key[("n", n.id)] = (sum(self.nodes[m].c + self.sc(self.nodes[m]) / 2 for m in nb) + own) / (len(nb) + 1)
        self._block_keys(key)
        return key

    def _block_keys(self, key):
        def members(bid):
            blk = self.blocks[bid]
            out = list(blk.nodes)
            for ch in blk.children:
                out += members(ch)
            return out
        for bid in self.blocks:
            if bid != ROOT:
                m = members(bid)
                key[("b", bid)] = sum(key[("n", n)] for n in m) / len(m)

    def run(self, iterations=8):
        best_cost, best = None, None
        for start in ("order", "reverse"):
            key = self.keys_from_order(reverse=(start == "reverse"))
            for it in range(iterations if start == "order" else max(2, iterations // 2)):
                cost = self.run_once(key)
                if best_cost is None or cost < best_cost - 1e-9:
                    best_cost, best = cost, self.snapshot()
                key = self.keys_from_barycenter()
        self.restore(best)
        self.place_labels()
        return self.result()

    # ------------------------------------------------------------ labels
    def place_labels(self):
        obstacles = []
        for n in self.nodes.values():
            x, y = self.to_xy(n.p, n.c)
            w, h = (self.sp(n), self.sc(n)) if self.lr else (self.sc(n), self.sp(n))
            obstacles.append((x - 2, y - 2, w + 4, h + 4, 10))
        for hp, hc, hsp, hsc in self.header_rects():
            x, y = self.to_xy(hp, hc)
            w, h = (hsp, hsc) if self.lr else (hsc, hsp)
            obstacles.append((x, y, w, h, 8))
        placed = []
        # every other connector's segments: a label must not sit where another line passes,
        # or the reader can't tell which line the text belongs to
        segs = [(e["id"], a, b) for e in self.edges for a, b in zip(e["path"], e["path"][1:])]
        for e in sorted(self.edges, key=lambda e: -len(e["path"])):
            e["label_pos"], e["label_rect"] = None, None
            if not e.get("label_box"):
                continue
            w, h = e["label_box"]
            pts = e["path"]
            total = sum(_dist(a, b) for a, b in zip(pts, pts[1:])) or 1.0
            best = None
            acc = 0.0
            others = [(a, b) for eid, a, b in segs if eid != e["id"]]
            for a, b in zip(pts, pts[1:]):
                seg = _dist(a, b)
                for f in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88):
                    if seg < 1:
                        continue
                    d = acc + f * seg
                    px, py = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                    r = (px - w / 2, py - h / 2, w, h)
                    score = sum(_overlap(r, ob[:4]) * ob[4] for ob in obstacles)
                    score += sum(_overlap(r, p) * 6 for p in placed)
                    score += 3000 * sum(1 for c, dd in others if _seg_hits_rect(c, dd, (r[0] + 1, r[1] + 1, r[2] - 2, r[3] - 2)))
                    score += abs(d / total - 0.5) * 40 + (0 if seg >= (w if abs(a[1] - b[1]) < 0.5 else h) else 15)
                    if best is None or score < best[0]:
                        best = (score, d, r)
                acc += seg
            if best:
                e["label_pos"] = 2 * best[1] / total - 1
                e["label_rect"] = best[2]
                placed.append(best[2])

    # ------------------------------------------------------------ output
    def result(self):
        out = {"nodes": {}, "boundaries": {}, "edges": {}, "layers": [], "warnings": self.warnings,
               "stats": self.stats}
        for n in self.nodes.values():
            x, y = self.to_xy(n.p, n.c)
            out["nodes"][n.id] = {"x": x, "y": y, "w": n.bw, "h": n.bh,
                                  "cx": x + n.cdx, "cy": y + n.cdy, "cw": n.cw, "ch": n.ch}
        for bid, b in self.blocks.items():
            if bid == ROOT:
                continue
            x, y = self.to_xy(b.p0, b.c0)
            w, h = (b.p1 - b.p0, b.size_c) if self.lr else (b.size_c, b.p1 - b.p0)
            out["boundaries"][bid] = {"x": x, "y": y, "w": w, "h": h, "header_h": b.header_h}
        for e in self.edges:
            def side_xy(side, f):
                if self.lr:
                    return {"pe": (1, f), "ps": (0, f), "ce": (f, 1), "cs": (f, 0)}[side]
                return {"pe": (f, 1), "ps": (f, 0), "ce": (1, f), "cs": (0, f)}[side]
            out["edges"][e["id"]] = {
                "exit": side_xy(e["su"], e["frac_u"]), "entry": side_xy(e["sv"], e["frac_v"]),
                "exit_dy": e.get("drop_u", 0.0), "entry_dy": e.get("drop_v", 0.0),
                "points": [self.to_xy(*b) for b in e["bends_a"]], "path": e["path"],
                "label_pos": e.get("label_pos"), "label_rect": e.get("label_rect")}
        for k in range(self.n_layers):
            if self.lr:
                out["layers"].append({"x": self.col_start[k], "w": self.col_w[k]})
            else:
                out["layers"].append({"y": self.col_start[k], "h": self.col_w[k]})
        return out


def _copy_tracks(tracks):
    out = []
    for tr in tracks:
        d = dict(tr)
        d["multi"] = list(tr["multi"])
        d["multi_cols"] = set(tr["multi_cols"])
        d["stacks"] = {c: list(st) for c, st in tr["stacks"].items()}
        out.append(d)
    return out


def _isotonic(vals):
    blocks = []
    for v in vals:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            sm, ct = blocks.pop()
            blocks[-1][0] += sm
            blocks[-1][1] += ct
    out = []
    for sm, ct in blocks:
        out += [sm / ct] * ct
    return out


def _snap(v, grid, lo, hi):
    g = round(v / grid) * grid
    if g < lo - 1e-6:
        g += grid
    if g > hi + 1e-6:
        g -= grid
    return min(max(g, lo), hi) if lo <= hi else lo


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _overlap(r1, r2):
    x0, y0 = max(r1[0], r2[0]), max(r1[1], r2[1])
    x1, y1 = min(r1[0] + r1[2], r2[0] + r2[2]), min(r1[1] + r1[3], r2[1] + r2[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _seg_hits_rect(a, b, r):
    x, y, w, h = r
    if w <= 0 or h <= 0:
        return False
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    return x0 < x + w and x1 > x and y0 < y + h and y1 > y
