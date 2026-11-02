"""Local icon library: manifest, alias resolution, embedding, fetching, importing.

Resolution never guesses: a technology maps to an icon only through its
canonical name or a listed alias (after removing vendor prefixes, version
numbers and generic words such as "db" or "cluster"). A named technology
that has no icon file is looked up online (logo sets only, exact names
only) and saved to the library; if no logo exists anywhere, the library
stores a grey tile showing the name. It never borrows another product's logo.
"""
import base64
import datetime
import html
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
ICON_ROOT = SKILL_ROOT / "assets" / "icons"
MANIFEST = ICON_ROOT / "icon-manifest.json"
USER_AGENT = "architecture-diagram-builder-skill/1.0 (+icon fetch)"

VENDOR_PREFIXES = ["microsoft azure", "amazon web services", "google cloud platform", "google cloud",
                   "azure", "amazon", "aws", "google", "gcp", "microsoft", "self hosted", "selfhosted",
                   "self managed", "managed", "hosted", "apache", "hashicorp", "the"]
SUFFIX_WORDS = {"db", "database", "databases", "server", "servers", "service", "services", "cluster",
                "clusters", "instance", "instances", "container", "containers", "broker", "brokers",
                "engine", "cache", "queue", "queues", "topic", "topics", "app", "apps", "application",
                "api", "platform", "repo", "repository", "pipeline", "pipelines", "deployment", "pod",
                "pods", "node", "nodes", "workspace", "bucket", "buckets", "store", "storage"}
VERSION_RE = re.compile(r"^v?\d+(\.\d+)*[a-z]?$")

# Component type -> generic icon. Several spellings per type.
TYPE_SYNONYMS = {
    "user": ["user", "person", "actor", "customer", "employee", "end user"],
    "users": ["users", "team", "people", "customers"],
    "web-app": ["web-app", "web app", "webapp", "web", "frontend", "front end", "spa", "browser", "portal", "website", "client", "web client"],
    "mobile": ["mobile", "mobile app", "mobile client", "ios app", "android app"],
    "desktop": ["desktop", "desktop app", "desktop client", "workstation"],
    "service": ["service", "microservice", "micro service", "app", "application", "backend", "worker", "component", "module"],
    "api": ["api", "rest api", "graphql", "grpc", "endpoint"],
    "gateway": ["gateway", "api gateway", "ingress", "reverse proxy", "proxy", "edge proxy"],
    "load-balancer": ["load-balancer", "load balancer", "lb", "alb"],
    "function": ["function", "functions", "serverless", "faas"],
    "container": ["container", "containers", "container registry", "registry"],
    "cluster": ["cluster", "orchestrator", "container orchestrator"],
    "server": ["server", "compute", "vm", "virtual machine", "host", "node", "instance", "bare metal"],
    "database": ["database", "db", "rdbms", "sql database", "nosql", "data store", "datastore", "vector database", "vector store", "vector db"],
    "cache": ["cache", "in memory cache", "session store"],
    "queue": ["queue", "message queue", "mq", "broker", "message broker"],
    "stream": ["stream", "event stream", "event bus", "streaming", "pubsub", "pub/sub", "topic", "events", "event hub"],
    "object-storage": ["object-storage", "object storage", "blob storage", "blob", "bucket", "storage", "data lake"],
    "file-storage": ["file-storage", "file storage", "file share", "nfs", "filesystem", "file system", "sftp"],
    "ai": ["ai", "ml", "model", "llm", "ai service", "ml model", "inference", "ai model", "agent", "ai agent", "genai", "embedding"],
    "external-system": ["external-system", "external", "external system", "third party", "third-party", "saas", "partner", "legacy", "erp", "crm"],
    "security": ["security", "waf", "policy", "scanner"],
    "identity": ["identity", "auth", "authentication", "iam", "idp", "identity provider", "sso", "oauth", "directory"],
    "secrets": ["secrets", "secret", "key vault", "kms", "vault", "certificates", "keys"],
    "firewall": ["firewall", "network firewall"],
    "monitoring": ["monitoring", "observability", "metrics", "alerting", "apm", "tracing"],
    "logging": ["logging", "logs", "log", "siem", "audit", "log store"],
    "dashboard": ["dashboard", "dashboards", "reporting ui"],
    "cicd": ["cicd", "ci/cd", "ci", "cd", "pipeline", "build", "deployment pipeline", "iac", "infrastructure as code"],
    "repository": ["repository", "repo", "source control", "scm", "git repo", "code repository", "artifact registry"],
    "network": ["network", "vnet", "vpc", "cdn", "dns", "internet", "edge", "service mesh", "subnet"],
    "email": ["email", "smtp", "mail"],
    "notification": ["notification", "notifications", "alerts", "push", "sms", "messaging service", "chat"],
    "scheduler": ["scheduler", "batch", "cron", "job", "jobs", "scheduled job"],
    "analytics": ["analytics", "data warehouse", "warehouse", "bi", "reporting", "olap", "lakehouse"],
    "cloud": ["cloud"],
    "datacenter": ["datacenter", "data center", "data centre", "on-prem", "on prem", "onprem", "on-premises", "on premises"],
    "device": ["device", "iot", "sensor", "edge device", "hardware"],
    "etl": ["etl", "elt", "integration", "data pipeline", "workflow", "orchestration", "esb", "integration bus"],
    "search": ["search", "search engine", "index", "search index"],
}


def normalize(s):
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"[_\-/]", " ", s).replace(".", "")
    s = re.sub(r"[()\[\],:;\"']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_TYPE_INDEX = {normalize(syn): t for t, syns in TYPE_SYNONYMS.items() for syn in syns}


def generic_type(ctype):
    """Canonical generic type for a component type string, or None."""
    if not ctype:
        return None
    n = normalize(ctype)
    if n in _TYPE_INDEX:
        return _TYPE_INDEX[n]
    if n.startswith("generic "):
        n = n[8:]
    return _TYPE_INDEX.get(n)


@dataclass
class Resolution:
    icon_key: str = None     # icon actually used (None = no icon)
    tech_key: str = None     # recognised canonical technology (may differ from icon_key)
    kind: str = "none"       # exact | alias | stripped | fallback | generic | none
    note: str = ""


def is_badge(entry):
    return entry.get("generated") == "name-badge"


class Library:
    def __init__(self, root=ICON_ROOT, online=None):
        self.root = Path(root)
        self.manifest_path = self.root / "icon-manifest.json"
        with open(self.manifest_path, encoding="utf-8") as f:
            self.entries = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        # ADB_OFFLINE=1 (or --offline) keeps all diagram work local
        self.online = (os.environ.get("ADB_OFFLINE", "").lower() not in ("1", "true", "yes")) if online is None else online
        self._build_index()

    # ------------------------------------------------------------ index
    def _build_index(self):
        self.index, self.collisions = {}, []
        # name badges go last and only claim names nobody else has, so a real logo always wins
        for key, e in sorted(self.entries.items(), key=lambda kv: is_badge(kv[1])):
            if e.get("category") == "generic" or e.get("badge_for"):
                continue
            for name in [key, e.get("label", "")] + list(e.get("aliases", [])):
                n = normalize(name)
                if not n:
                    continue
                if n in self.index and self.index[n] != key:
                    if not is_badge(e):
                        self.collisions.append((n, self.index[n], key))
                    continue
                self.index[n] = key

    def save(self):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=1, ensure_ascii=False)
            f.write("\n")
        self._build_index()

    # ------------------------------------------------------------ files
    def path(self, key):
        e = self.entries.get(key)
        return (self.root / e["icon"]) if e else None

    def available(self, key):
        p = self.path(key)
        return bool(p and p.is_file() and p.stat().st_size > 0)

    def label(self, key):
        return self.entries.get(key, {}).get("label", key)

    def svg_bytes(self, key):
        return self.path(key).read_bytes()

    def data_uri(self, key):
        """draw.io style value for an embedded SVG (draw.io omits ';base64' because ';' separates style keys)."""
        return "data:image/svg+xml," + base64.b64encode(self.svg_bytes(key)).decode("ascii")

    # ------------------------------------------------------------ resolution
    def generic_for(self, ctype):
        t = generic_type(ctype)
        key = f"generic-{t}" if t else None
        return key if key in self.entries and self.available(key) else None

    def _candidates(self, tech):
        seen = []

        def push(s, how):
            s = s.strip()
            if s and s not in [c for c, _ in seen]:
                seen.append((s, how))

        n = normalize(tech)
        push(n, "exact")
        toks = n.split()
        while toks and VERSION_RE.match(toks[-1]):
            toks = toks[:-1]
        base = " ".join(toks)
        push(base, "stripped")
        variants = [base]
        for pre in VENDOR_PREFIXES:
            if base.startswith(pre + " "):
                rest = base[len(pre) + 1:]
                push(rest, "stripped")
                variants.append(rest)
                break
        for v in variants:
            t = v.split()
            while len(t) > 1 and t[-1] in SUFFIX_WORDS:
                t = t[:-1]
                push(" ".join(t), "stripped")
        return seen

    def lookup(self, tech):
        """Canonical key for a technology name, or None. No fuzzy matching."""
        for cand, how in self._candidates(tech):
            if cand in self.index:
                return self.index[cand], how
        return None, None

    def resolve(self, tech=None, ctype=None, create=True):
        """Icon for a component. With `create`, a named technology that has no icon file is looked up
        online and saved, or gets a saved name badge; validation passes create=False so it never writes."""
        if tech:
            key, how = self.lookup(tech)
            if key and is_badge(self.entries[key]) and self.available(key):
                return Resolution(key, key, "name", f"no logo for '{tech}' found online; showing its name "
                                                    f"(`icons find \"{tech}\"` tries again)")
            if key:
                kind = "exact" if how == "exact" and normalize(tech) in (normalize(key), normalize(self.label(key))) else \
                       ("alias" if how == "exact" else "stripped")
                if self.available(key):
                    return Resolution(key, key, kind, "")
                badge = self.badge_for(key, create=False)
                if badge:  # searched before and nothing found; `icons fetch` or `icons find` look again
                    return Resolution(badge, key, "name", f"no {self.label(key)} logo yet; showing its name")
                if create and self.download_known(key):
                    return Resolution(key, key, kind, f"{self.label(key)} logo downloaded to the library")
                chain, seen = [], {key}
                fb = self.entries[key].get("fallback")
                while fb and fb not in seen and fb in self.entries:
                    seen.add(fb)
                    if self.available(fb):
                        return Resolution(fb, key, "fallback",
                                          f"{self.label(key)} icon not in the library yet; used the {self.label(fb)} icon (same technology)")
                    fb = self.entries[fb].get("fallback")
                badge = self.badge_for(key, create)
                if badge:
                    why = "found online" if self.online else f"yet, working offline ({self._how_to_get(key)})"
                    return Resolution(badge, key, "name", f"no {self.label(key)} logo {why}; showing its name")
                g = self.generic_for(ctype) or self.generic_for(self.entries[key].get("type"))
                src = self._how_to_get(key)
                return Resolution(g, key, "generic" if g else "none",
                                  f"{self.label(key)} is recognised but its icon file is missing ({src})" +
                                  ("; used a generic icon" if g else ""))
            if create and not generic_type(tech):
                found = self.discover(tech, ctype)
                if found:
                    return Resolution(found, found, "found", f"{tech} logo downloaded from {self.entries[found]['source_url']}")
                badge = self.make_badge(tech, ctype)
                return Resolution(badge, badge, "name", f"no logo for '{tech}' found online; showing its name")
            g = self.generic_for(ctype) or self.generic_for(tech)
            return Resolution(g, None, "generic" if g else "none",
                              f"no icon for '{tech}' in the library" + ("; used a generic icon" if g else "; no type given, so no icon"))
        g = self.generic_for(ctype)
        return Resolution(g, None, "generic" if g else "none", "" if g else "no technology or type given")

    def allowed_icons(self, tech, ctype):
        """Icon keys that are acceptable for a component (used by validation)."""
        allowed = set()
        res = self.resolve(tech, ctype, create=False)
        if res.icon_key:
            allowed.add(res.icon_key)
        if res.tech_key:
            allowed.add(res.tech_key)
            fb = self.entries[res.tech_key].get("fallback")
            while fb and fb not in allowed:
                allowed.add(fb)
                fb = self.entries.get(fb, {}).get("fallback")
            g = self.generic_for(self.entries[res.tech_key].get("type"))
            if g:
                allowed.add(g)
            # the name badge the technology had before its logo arrived
            allowed.update(k for k, e in self.entries.items() if e.get("badge_for") == res.tech_key)
        g = self.generic_for(ctype)
        if g:
            allowed.add(g)
        return allowed

    # ------------------------------------------------------------ online lookup and name badges
    def _go_online(self, fn):
        """Run a network step; after the first network failure stay offline for the rest of the run."""
        if not self.online:
            return None
        try:
            return fn()
        except (OSError, ValueError) as exc:
            self.online = False
            self.offline_reason = str(exc)
            return None

    def download_known(self, key):
        """Get the missing file of a manifest entry: its own sources first, then the Iconify logo sets."""
        e = self.entries[key]
        if e.get("sources"):
            self._go_online(lambda: fetch(self, only=[key], log=lambda *a: None))
            if self.available(key):
                return True
        hit = self._go_online(lambda: find_online([key, e.get("label", "")] + list(e.get("aliases", []))))
        if not hit:
            return False
        svg, url, lic = hit
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(svg)
        e.update({"source_url": url, "license": lic, "retrieved": datetime.date.today().isoformat()})
        self.save()
        return True

    def discover(self, tech, ctype=None):
        """Find a logo online for a technology the manifest doesn't know; save it as a new entry."""
        hit = self._go_online(lambda: find_online([tech]))
        if not hit:
            return None
        svg, url, lic = hit
        key = self._free_key(slug(tech))
        rel = f"found/{key}.svg"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(svg)
        self.entries[key] = {"label": tech.strip(), "aliases": [], "category": "found",
                             "type": generic_type(ctype) or "service", "icon": rel, "source_url": url,
                             "license": lic, "retrieved": datetime.date.today().isoformat()}
        for k, e in self.entries.items():
            if is_badge(e) and not e.get("badge_for") and normalize(e["label"]) == normalize(tech):
                e["badge_for"] = key
        self.save()
        return key

    def badge_for(self, key, create=True):
        """Name badge standing in for a manifest entry whose logo can't be found."""
        bkey = f"name-{key}"
        if bkey in self.entries and self.available(bkey):
            return bkey
        return self.make_badge(self.label(key), self.entries[key].get("type"), for_key=key) if create else None

    def make_badge(self, name, ctype=None, for_key=None):
        name = re.sub(r"\s+", " ", str(name)).strip()
        bkey = f"name-{for_key}" if for_key else self._free_key("name-" + slug(name))
        rel = f"names/{bkey[5:]}.svg"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(name_badge_svg(name))
        e = {"label": name, "aliases": [], "category": "names", "type": generic_type(ctype) or "service",
             "icon": rel, "generated": "name-badge", "source_url": "generated by this skill",
             "license": "Generated text tile, free to use", "retrieved": datetime.date.today().isoformat()}
        if for_key:
            e["badge_for"] = for_key
        self.entries[bkey] = e
        self.save()
        return bkey

    def _free_key(self, key):
        key = key or "icon"
        n, out = 2, key
        while out in self.entries:
            out, n = f"{key}-{n}", n + 1
        return out

    def retry_badges(self, only=None, log=print):
        """Look online again for every technology currently shown as a name badge."""
        found, still = [], []
        for bkey, e in list(self.entries.items()):
            if not is_badge(e) or (only and normalize(e["label"]) not in only):
                continue
            real = e.get("badge_for")
            if real:
                if self.available(real) or self.download_known(real):
                    found.append((e["label"], real))
                else:
                    still.append(e["label"])
            else:
                key = self.discover(e["label"], e.get("type"))
                (found.append((e["label"], key)) if key else still.append(e["label"]))
        for label, key in found:
            log(f"  found {label:<24} -> {key} ({self.entries[key].get('source_url', '')})")
        if not self.online:
            log(f"  offline: {getattr(self, 'offline_reason', 'ADB_OFFLINE is set')}")
        return found, still

    def _how_to_get(self, key):
        e = self.entries[key]
        if e.get("pack"):
            return f"import the official {e['pack'].upper()} icon pack with `icons import --provider {e['pack']}`"
        if e.get("sources"):
            return f"run `icons fetch --only {key}` where internet access is available"
        return "add it with `icons add`"

    def detect_in_label(self, text):
        """Technologies named in a free-text label. Returns list of (segment, key)."""
        found = []
        for seg in re.split(r"[\n()\[\]|,/·•–—]| - ", text or ""):
            seg = seg.strip()
            if not seg or len(seg) > 60:
                continue
            key, _ = self.lookup(seg)
            if key and key not in [k for _, k in found]:
                found.append((seg, key))
        return found


# ---------------------------------------------------------------- SVG hygiene
def sanitize_svg(data):
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = re.sub(r"(?is)<script.*?</script>", "", text)
    text = re.sub(r"(?is)<foreignObject.*?</foreignObject>", "", text)
    text = re.sub(r"(?i)\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*')", "", text)
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    m = re.search(r"<svg\b[^>]*>", text)
    if not m:
        raise ValueError("not an SVG file")
    tag = m.group(0)
    if "viewBox" not in tag:
        w = re.search(r'\swidth="([\d.]+)(px)?"', tag)
        h = re.search(r'\sheight="([\d.]+)(px)?"', tag)
        if w and h:
            text = text.replace(tag, tag[:-1] + f' viewBox="0 0 {w.group(1)} {h.group(1)}">', 1)
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", text).encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"SVG is not well-formed XML: {exc}")
    return text.strip().encode("utf-8")


# ---------------------------------------------------------------- fetching
DEVICON_INDEX = ["https://cdn.jsdelivr.net/gh/devicons/devicon@latest/devicon.json",
                 "https://raw.githubusercontent.com/devicons/devicon/master/devicon.json"]
DEVICON_ICON = ["https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/{n}/{n}-{v}.svg",
                "https://raw.githubusercontent.com/devicons/devicon/master/icons/{n}/{n}-{v}.svg"]
SIMPLEICONS = ["https://cdn.simpleicons.org/{s}",
               "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{s}.svg"]
VARIANTS = ["original", "plain", "original-wordmark", "plain-wordmark", "line"]
LICENSES = {"devicon": "MIT (Devicon). The logo is a trademark of its owner.",
            "simpleicons": "CC0-1.0 (Simple Icons). The logo is a trademark of its owner."}

# Online lookup goes through the Iconify API and only searches sets of product logos,
# best-looking first, so a technology name can never pick up a general UI glyph.
ICONIFY_API = "https://api.iconify.design"
ICONIFY_SETS = {
    "devicon": "MIT (Devicon, via Iconify). The logo is a trademark of its owner.",
    "logos": "CC0-1.0 (SVG Logos, via Iconify). The logo is a trademark of its owner.",
    "skill-icons": "MIT (Skill Icons, via Iconify). The logo is a trademark of its owner.",
    "thesvg-color": "MIT (theSVG Color, via Iconify). The logo is a trademark of its owner.",
    "simple-icons": "CC0-1.0 (Simple Icons, via Iconify). The logo is a trademark of its owner.",
    "devicon-plain": "MIT (Devicon plain, via Iconify). The logo is a trademark of its owner.",
    "thesvg": "MIT (theSVG, via Iconify). The logo is a trademark of its owner.",
}


def _compact(s):
    return re.sub(r"[^a-z0-9]", "", normalize(s))


def search_terms(names):
    """What to search for: each name without versions or trailing generic words ("db", "server"),
    with and without spaces. Vendor prefixes are kept, since "Azure Functions" is not "Functions"."""
    terms = []
    for name in names:
        toks = normalize(name).split()
        while toks and VERSION_RE.match(toks[-1]):
            toks = toks[:-1]
        while toks:
            for t in (" ".join(toks), "".join(toks)):
                if len(t) >= 3 and t not in terms:
                    terms.append(t)
            if len(toks) > 1 and toks[-1] in SUFFIX_WORDS:
                toks = toks[:-1]
            elif len(toks) == 1 and toks[0].endswith("db") and len(toks[0]) > 4:
                toks = [toks[0][:-2]]          # timescaledb -> timescale
            else:
                break
    return terms


def find_online(names, get=None):
    """(svg, url, licence) for the first of `names` with a logo in the Iconify logo sets, else None.
    An icon counts only when its own name is the technology name (or that name plus "-icon");
    near matches such as "dcss" for "dcs" are refused."""
    get = get or http_get
    rank = list(ICONIFY_SETS)
    for term in search_terms(names):
        q = urllib.parse.urlencode({"query": term, "limit": 64, "prefixes": ",".join(rank)})
        hits = json.loads(get(f"{ICONIFY_API}/search?{q}")).get("icons", [])
        want, good = _compact(term), []
        for full in hits:
            prefix, _, name = full.partition(":")
            if prefix not in ICONIFY_SETS:
                continue
            symbol = name.endswith("-icon")    # logos:docker is the wordmark, logos:docker-icon the symbol
            if _compact(name[:-5] if symbol else name) == want:
                good.append((rank.index(prefix), 0 if symbol else 1, prefix, name))
        for _, _, prefix, name in sorted(good):
            try:
                svg, url = _try_svg([f"{ICONIFY_API}/{prefix}/{name}.svg"], get)
            except RuntimeError:
                continue
            return _sized(svg), url, ICONIFY_SETS[prefix]
    return None


def _sized(svg):
    """Iconify serves width="1em"; give the SVG its viewBox size so draw.io scales it properly."""
    text = svg.decode("utf-8")
    m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"', text)
    if m:
        tag = re.search(r"<svg\b[^>]*>", text).group(0)
        new = re.sub(r'\s(width|height)="[^"]*"', "", tag)
        new = new.replace("<svg", f'<svg width="{m.group(1)}" height="{m.group(2)}"', 1)
        text = text.replace(tag, new, 1)
    return text.encode("utf-8")


def name_badge_svg(name):
    """Grey tile showing a technology's name, used when no logo exists anywhere.
    Neutral colours on purpose, so it can't be mistaken for a brand."""
    words = name.split()
    lines = [name]
    if len(name) > 9 and len(words) > 1:
        cut = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    longest = max(len(ln) for ln in lines)
    fs = max(7.0, min(20.0, 56 / (0.62 * longest), 50 / (1.25 * len(lines))))
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">',
             '<rect x="1" y="1" width="62" height="62" rx="10" fill="#eef1f5" stroke="#9aa5b1" stroke-width="1.5"/>']
    for i, ln in enumerate(lines):
        y = 32 + (i - (len(lines) - 1) / 2) * fs * 1.2 + fs * 0.35
        fit = ' textLength="56" lengthAdjust="spacingAndGlyphs"' if 0.62 * len(ln) * fs > 56 else ""
        parts.append(f'<text x="32" y="{y:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="{fs:.1f}" '
                     f'font-weight="bold" fill="#2d3748" text-anchor="middle"{fit}>{html.escape(ln)}</text>')
    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _try_svg(urls, get):
    last = None
    for url in urls:
        try:
            data = get(url)
            if b"<svg" not in data[:4000]:
                raise ValueError("response is not SVG")
            if len(data) > 600_000:
                raise ValueError("SVG larger than 600 KB")
            return sanitize_svg(data), url
        except Exception as exc:  # network errors, 404s, bad content
            last = exc
    raise RuntimeError(str(last) if last else "no URL to try")


def fetch(lib, only=None, category=None, force=False, prefer="devicon", get=http_get, log=print):
    keys = [k for k, e in lib.entries.items() if e.get("sources")
            and (not only or k in only) and (not category or e.get("category") == category)]
    if only:
        unknown = [k for k in only if k not in lib.entries]
        for k in unknown:
            log(f"  unknown key: {k}")
    dev_index = None
    if any("devicon" in lib.entries[k]["sources"] for k in keys):
        for url in DEVICON_INDEX:
            try:
                dev_index = {d["name"]: d for d in json.loads(get(url))}
                break
            except Exception:
                continue
    done, skipped, failed = [], [], []
    for key in keys:
        e = lib.entries[key]
        dest = lib.path(key)
        if dest.is_file() and not force:
            skipped.append(key)
            continue
        order = ["devicon", "simpleicons"] if prefer == "devicon" else ["simpleicons", "devicon"]
        result, errors = None, []
        for src in order:
            names = e["sources"].get(src, [])
            for name in names:
                try:
                    if src == "devicon":
                        if dev_index is not None:
                            if name not in dev_index:
                                raise RuntimeError(f"devicon has no '{name}'")
                            avail = dev_index[name].get("versions", {}).get("svg", [])
                            variants = [v for v in VARIANTS if v in avail] or avail[:1]
                        else:
                            variants = VARIANTS[:2]
                        urls = [u.format(n=name, v=v) for v in variants for u in DEVICON_ICON]
                    else:
                        urls = [u.format(s=name) for u in SIMPLEICONS]
                    svg, url = _try_svg(urls, get)
                    result = (svg, url, src)
                    break
                except Exception as exc:
                    errors.append(f"{src}:{name}: {exc}")
            if result:
                break
        if not result:
            failed.append((key, "; ".join(errors) or "no sources"))
            continue
        svg, url, src = result
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(svg)
        e.update({"source_url": url, "license": LICENSES[src], "retrieved": datetime.date.today().isoformat()})
        done.append(key)
        log(f"  fetched {key:<22} {url}")
    lib.save()
    log(f"\nFetched {len(done)}, already present {len(skipped)}, failed {len(failed)}.")
    for key, why in failed:
        log(f"  FAILED {key}: {why[:300]}")
    if failed:
        log("Diagrams that need a failed icon search the other logo sets for it, else show its name. "
            "To use your own file: `icons add KEY --file icon.svg --force`.")
    return done, skipped, failed


# ---------------------------------------------------------------- adding / importing
def slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def add_icon(lib, key, file=None, url=None, label=None, aliases=(), category=None, ctype=None,
             license_text=None, get=http_get, force=False):
    if not file and not url:
        raise ValueError("give --file or --url")
    data = Path(file).read_bytes() if file else get(url)
    svg = sanitize_svg(data)
    e = lib.entries.get(key)
    if e is None:
        if not category:
            raise ValueError("new icons need --category")
        e = {"label": label or key, "aliases": [], "category": category, "type": ctype or "service",
             "icon": f"{category}/{key}.svg"}
        lib.entries[key] = e
    dest = lib.path(key)
    if dest.is_file() and not force:
        raise ValueError(f"{dest} exists; use --force to replace it")
    clashes = []
    for a in aliases:
        n = normalize(a)
        owner = lib.index.get(n)
        if owner and owner != key:
            clashes.append(f"'{a}' already means {owner}")
        elif a not in e["aliases"]:
            e["aliases"].append(a)
    if label:
        e["label"] = label
    if ctype:
        e["type"] = ctype
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(svg)
    e.update({"source_url": url or f"local file {Path(file).name}",
              "license": license_text or "Supplied by the user; check the owner's terms before sharing",
              "retrieved": datetime.date.today().isoformat()})
    lib.save()
    return dest, clashes


_DROP_TAIL = {"light", "dark", "color", "colour", "rgb", "icon", "logo", "svg", "bg", "fg", "fullcolor", "full"}
_PROVIDER_WORDS = {"aws": ["amazon", "aws"], "azure": ["azure", "microsoft"], "gcp": ["google", "gcp"]}


def clean_pack_name(filename):
    stem = Path(filename).stem
    s = re.sub(r"(?i)^\d+[-_]icon[-_]service[-_]", "", stem)
    s = re.sub(r"^(Arch|Res|Arch-Category)[_-]", "", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    toks = re.sub(r"[_\-.]+", " ", s).lower().split()
    while toks and (toks[-1].isdigit() or toks[-1] in _DROP_TAIL):
        toks.pop()
    return " ".join(toks)


def _cmp(s, provider=None, depluralize=False):
    toks = normalize(s).split()
    if provider:
        drop = _PROVIDER_WORDS.get(provider, [])
        while len(toks) > 1 and toks[0] in drop:
            toks = toks[1:]
    k = "".join(toks)
    return k[:-1] if depluralize and k.endswith("s") else k


def _pack_score(path):
    stem = Path(path).stem
    sizes = [int(x) for x in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", stem)]
    return (0 if stem.startswith("Res_") else 1, 1 if "light" in stem.lower() else 0, max(sizes) if sizes else 0)


def import_pack(lib, provider, folder, dry_run=False, log=print):
    provider = provider.lower()
    if provider not in ("aws", "azure", "gcp"):
        raise ValueError("provider must be aws, azure or gcp")
    files = [p for p in Path(folder).rglob("*.svg") if not p.name.startswith("Arch-Category_")]
    if not files:
        raise ValueError(f"no .svg files under {folder}")
    best = {}
    for p in files:
        name = clean_pack_name(p.name)
        if not name:
            continue
        if name not in best or _pack_score(p) > _pack_score(best[name]):
            best[name] = p
    curated = {k: e for k, e in lib.entries.items() if e.get("pack") == provider}
    lookup = {}
    for depl in (False, True):
        for k, e in curated.items():
            for n in list(e.get("pack_names", [])) + list(e.get("aliases", [])) + [e["label"]]:
                lookup.setdefault((depl, _cmp(n, provider, depl)), k)
    matched, created, skipped = {}, [], []
    for name, p in sorted(best.items()):
        key = lookup.get((False, _cmp(name, provider))) or lookup.get((True, _cmp(name, provider, True)))
        if key:
            if key not in matched or _pack_score(p) > _pack_score(matched[key][1]):
                matched[key] = (name, p)
            continue
        bare = name.split()
        while len(bare) > 1 and bare[0] in _PROVIDER_WORDS[provider]:
            bare = bare[1:]
        new_key = f"{provider}-{slug(' '.join(bare))}"
        if new_key in lib.entries:
            skipped.append((name, f"{new_key} already exists"))
            continue
        created.append((new_key, name, p))
    log(f"{len(files)} SVG files, {len(best)} distinct icons: {len(matched)} match curated entries, "
        f"{len(created)} new entries, {len(skipped)} skipped.")
    for key, (name, p) in sorted(matched.items()):
        log(f"  {key:<28} <- {p.name}")
    missing = sorted(set(curated) - set(matched))
    if missing:
        log(f"Curated {provider} entries with no file in this pack ({len(missing)}): {', '.join(missing)}")
    if dry_run:
        log("(dry run: nothing written)")
        return matched, created, missing
    stamp = datetime.date.today().isoformat()
    lic = {"aws": "AWS Architecture Icons (official). Use per AWS's terms for architecture diagrams.",
           "azure": "Microsoft Azure architecture icons (official). Use per Microsoft's icon terms.",
           "gcp": "Google Cloud icons (official). Use per Google's brand and icon terms."}[provider]
    for key, (name, p) in matched.items():
        dest = lib.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(sanitize_svg(p.read_bytes()))
        lib.entries[key].update({"source_url": f"{provider} pack: {p.name}", "license": lic, "retrieved": stamp})
    for new_key, name, p in created:
        label = " ".join(w.upper() if len(w) <= 3 and w not in ("for", "and", "the") else w.capitalize() for w in name.split())
        aliases = []
        for a in {name, " ".join(normalize(name).split()[1:]) if normalize(name).split()[0] in _PROVIDER_WORDS[provider] else ""}:
            if a and normalize(a) not in lib.index and len(a) > 2:
                aliases.append(a)
        rel = f"cloud/{provider}/{new_key[len(provider) + 1:]}.svg"
        try:
            svg = sanitize_svg(p.read_bytes())
        except ValueError as exc:
            log(f"  skipped {p.name}: {exc}")
            continue
        dest = lib.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(svg)
        lib.entries[new_key] = {"label": label, "aliases": aliases, "category": "cloud", "type": "service",
                                "icon": rel, "pack": provider, "source_url": f"{provider} pack: {p.name}",
                                "license": lic, "retrieved": stamp}
        lib._build_index()
    lib.save()
    log(f"Imported {len(matched)} curated and {len(created)} additional {provider} icons.")
    return matched, created, missing


# ---------------------------------------------------------------- gallery / lint
def gallery(lib, out):
    cats = {}
    for k, e in sorted(lib.entries.items(), key=lambda kv: (kv[1]["category"], kv[0])):
        cats.setdefault(e["category"], []).append(k)
    have = sum(1 for k in lib.entries if lib.available(k))
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><title>Icon library</title><style>
:root{{--bg:#fff;--fg:#1f2937;--muted:#6b7280;--card:#f8fafc;--line:#e2e8f0;--miss:#fef2f2}}
@media (prefers-color-scheme: dark){{:root{{color-scheme:dark;--bg:#0f172a;--fg:#e5e7eb;--muted:#9ca3af;--card:#1e293b;--line:#334155;--miss:#3b1d1d}}}}
body{{font-family:Helvetica,Arial,sans-serif;background:var(--bg);color:var(--fg);margin:24px}}
h2{{margin-top:28px;font-size:16px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:12px}}
.c.m{{background:var(--miss)}} .i{{height:48px;display:flex;align-items:center;justify-content:center;margin-bottom:6px}}
.i img{{max-height:48px;max-width:100px;background:#fff;border-radius:4px;padding:2px}}
.k{{font-weight:bold}} .a,.s{{color:var(--muted);font-size:11px;margin-top:3px;word-break:break-word}}
</style></head><body><h1>Icon library</h1><p>{have} of {len(lib.entries)} icons available locally.
Missing ones (shaded) are searched online when a diagram needs them. "found" holds logos downloaded that way;
"names" holds the name tiles used where no logo exists anywhere.</p>"""]
    for cat, keys in cats.items():
        parts.append(f"<h2>{html.escape(cat)}</h2><div class='grid'>")
        for k in keys:
            e = lib.entries[k]
            ok = lib.available(k)
            img = f"<img src='data:image/svg+xml;base64,{base64.b64encode(lib.svg_bytes(k)).decode()}'>" if ok else "missing"
            al = ", ".join(e.get("aliases", [])[:6])
            src = e.get("license", "") if ok else (lib._how_to_get(k) if e.get("category") != "generic" else "")
            parts.append(f"<div class='c{'' if ok else ' m'}'><div class='i'>{img}</div><div class='k'>{html.escape(k)}</div>"
                         f"<div>{html.escape(e.get('label', ''))}</div><div class='a'>{html.escape(al)}</div>"
                         f"<div class='s'>{html.escape(src)}</div></div>")
        parts.append("</div>")
    parts.append("</body></html>")
    Path(out).write_text("".join(parts), encoding="utf-8")
    return have, len(lib.entries)


def lint(lib):
    problems = [f"alias '{n}' used by both {a} and {b}" for n, a, b in lib.collisions]
    referenced = set()
    for k, e in lib.entries.items():
        p = lib.path(k)
        referenced.add(p.resolve())
        if not str(p.resolve()).startswith(str(lib.root.resolve())):
            problems.append(f"{k}: icon path escapes the icon folder")
        if p.is_file():
            try:
                sanitize_svg(p.read_bytes())
            except ValueError as exc:
                problems.append(f"{k}: {exc}")
        if e.get("fallback") and e["fallback"] not in lib.entries:
            problems.append(f"{k}: fallback '{e['fallback']}' is not in the manifest")
    stray = [str(p.relative_to(lib.root)) for p in lib.root.rglob("*.svg") if p.resolve() not in referenced]
    return problems, stray
