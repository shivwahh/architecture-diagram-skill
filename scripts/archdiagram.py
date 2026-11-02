#!/usr/bin/env python3
"""Architecture Diagram Builder: create, edit, check and preview native draw.io diagrams.

  build SPEC.json -o OUT.drawio [--preview OUT.png] [--target slide|document|screen]
                                                           new diagram from an architecture spec
  inspect FILE.drawio [--json] [--page NAME]               summarise an existing diagram
  patch FILE.drawio OPS.json -o OUT.drawio [--preview P] [--target T]
                                                           apply targeted edits to an existing diagram
  apply-icons FILE.drawio -o OUT.drawio [--map id=tech,..] [--dry-run] [--refresh]
  check FILE.drawio [--spec SPEC.json] [--compare BEFORE.drawio] [--page NAME] [--target T]
        [--covers ORIGINAL.drawio [--covers-page NAME] [--ignore id,..]]
                                                           --covers: fail if any original component is missing
  preview FILE.drawio -o OUT.png|OUT.svg [--page NAME]
  tidy-lines FILE.drawio -o OUT.drawio [--page NAME | --all-pages]
                                                           U-bend line jumps at crossings; labels moved off other lines
  export FILE.drawio -o OUT.png|OUT.svg [--page NAME] [--target slide]
                                                           image for PowerPoint/Word; slide = 1920x1080 PNG
  decode IN.drawio OUT.drawio                              write an uncompressed copy
  icons resolve NAME [--type TYPE] | find NAME [--type TYPE] | list [--category C] [--missing]
        | fetch [--only k1,k2] [--category C] [--force] [--prefer devicon|simpleicons]
        | add KEY (--file F | --url U) [--label L] [--aliases a,b] [--category C] [--type T] [--license TEXT] [--force]
        | import --provider aws|azure|gcp --dir DIR [--dry-run] | gallery -o FILE.html | lint

  --offline (before the command) keeps everything local: missing logos become name tiles
  without searching online. Setting ADB_OFFLINE=1 does the same.
"""
import os
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adb import drawio as D  # noqa: E402
from adb.icons import Library  # noqa: E402


def _preview(page, path):
    from adb.render import preview
    out = preview(page, path)
    note = "" if out == str(path) else " (PNG converter not found, wrote SVG instead)"
    print(f"Preview: {out}{note}")


def _readability(page, target):
    from adb.build import theme_for
    from adb.readability import measure, report_lines
    th = theme_for(target)
    for line in report_lines(measure(page, target, th), th["targets"].get(target)):
        print(line)


def cmd_build(a):
    from adb.build import build_for_target, SpecError
    from adb.check import check_file
    from adb.readability import report_lines
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    if a.target:
        spec["target"] = a.target
    lib = Library()
    try:
        tree, rep = build_for_target(spec, lib)
    except SpecError as exc:
        print("Spec problems:\n  " + str(exc).replace("\n", "\n  "))
        return 2
    D.save(tree, a.out)
    s = rep["stats"]
    print(f"Wrote {a.out}: {rep['components']} components, {rep['flows']} flows, {rep['boundaries']} boundaries, "
          f"{rep['direction']} layout, '{rep['style']}' icon style, target {rep['target']}.")
    if len(rep["tried"]) > 1:
        print("Tried (all components in every layout): " +
              ", ".join(f"{d} {m.get('min_pt')} pt" for d, m in rep["tried"]) +
              f"; kept {rep['direction']}/{rep['style']}.")
    for line in report_lines(rep["readability"], rep["target_cfg"]):
        print(line)
    if rep.get("without_details"):
        print(f"  option: names only, no detail lines (\"show_details\": false) -> smallest text "
              f"{rep['without_details']} pt, all boxes kept. Ask the user before using it.")
    print(f"Routing: {s['crossings']} crossings, {s['bends']} bends, {s['overlaps']} overlapping segments, "
          f"{s['through']} connectors through shapes.")
    for w in rep["warnings"]:
        print(f"  note: {w}")
    if rep["icon_notes"]:
        print("Icons:")
        for n in rep["icon_notes"]:
            print(f"  {n}")
    print("\nValidation:")
    code = check_file(a.out, spec=spec, lib=lib)
    if a.preview:
        _, pages = D.load(a.out)
        _preview(pages[0], a.preview)
    return code


def cmd_inspect(a):
    from adb.edit import inspect
    print(inspect(a.file, a.page, as_json=a.json))
    return 0


def cmd_patch(a):
    from adb.edit import patch
    from adb.check import check_file
    ops = json.loads(Path(a.ops).read_text(encoding="utf-8"))
    code = patch(a.file, ops, a.out, a.page)
    if code:
        return code
    # new connectors: U-bend jumps, and no label left with another line running through it
    from adb.tidy import tidy_page
    _, old_pages = D.load(a.file)
    old_ids = set(D.pick_page(old_pages, a.page).by_id)
    tree, new_pages = D.load(a.out)
    pg = D.pick_page(new_pages, a.page)
    added = {cid for cid, _, c in pg.items if c.get("edge") == "1" and cid not in old_ids}
    if added:
        r = tidy_page(pg, only=added)
        D.save(tree, a.out)
        if r["labels_moved"]:
            print(f"  moved {len(r['labels_moved'])} label(s) so no line runs through them")
    print("\nValidation (compared with the original):")
    code = check_file(a.out, page_name=a.page, compare_path=a.file, lib=Library(), baseline=True)
    _, pages = D.load(a.out)
    if a.target:
        _readability(D.pick_page(pages, a.page), a.target)
    if a.preview:
        _preview(D.pick_page(pages, a.page), a.preview)
    return code


def cmd_apply_icons(a):
    from adb.edit import apply_icons
    mapping = {}
    for item in (a.map or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            mapping[k.strip()] = v.strip()
    return apply_icons(a.file, a.out, a.page, mapping, a.dry_run, a.refresh)


def cmd_check(a):
    from adb.check import check_file
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8")) if a.spec else None
    code = check_file(a.file, a.page, spec, a.compare, Library())
    _, pages = D.load(a.file)
    if a.covers:
        from adb.coverage import compare
        _, old_pages = D.load(a.covers)
        old = D.pick_page(old_pages, a.covers_page)
        ignore = {x.strip() for x in (a.ignore or "").split(",") if x.strip()}
        missing, total, ignored = compare(old, D.pick_page(pages, a.page), ignore)
        scope = f" ({ignored} left out on purpose with --ignore)" if ignored else ""
        if missing:
            print(f"\nCompleteness: {len(missing)} of {total} components of '{old.name}' are missing{scope}:")
            for cid, name in missing:
                print(f"  MISSING: {name} ({cid})")
            print("Readability work must keep every component. Add them back (or, only if the user agreed "
                  "to leave one out, list it with --ignore).")
            code = code or 1
        else:
            print(f"\nCompleteness: all {total} components of '{old.name}' are present{scope}.")
    if a.target:
        _readability(D.pick_page(pages, a.page), a.target)
    return code


def cmd_tidy_lines(a):
    from adb.tidy import tidy_page
    from adb.check import check_file
    tree, pages = D.load(a.file)
    targets = pages if a.all_pages else [D.pick_page(pages, a.page)]
    for page in targets:
        r = tidy_page(page)
        print(f"[{page.name}] line jumps added to {r['jumps_added']} connector(s); "
              f"{len(r['labels_moved'])} label(s) moved; lines through labels {r['through_before']} -> {r['through_after']}")
    D.save(tree, a.out)
    print(f"Wrote {a.out}\n\nValidation:")
    return check_file(a.out, None if a.all_pages else a.page, None, a.file, Library())


def cmd_export(a):
    from adb.render import export
    _, pages = D.load(a.file)
    page = D.pick_page(pages, a.page)
    canvas = (1920, 1080) if a.target == "slide" and Path(a.out).suffix.lower() == ".png" else None
    out, how = export(page, pages.index(page) + 1, a.file, a.out, canvas)
    if not out:
        print(f"Export failed: {how}")
        return 1
    print(f"Exported '{page.name}' to {out} ({how})")
    if a.target:
        _readability(page, a.target)
    return 0


def cmd_preview(a):
    _, pages = D.load(a.file)
    _preview(D.pick_page(pages, a.page), a.out)
    return 0


def cmd_decode(a):
    tree, pages = D.load(a.file)
    D.save(tree, a.out)
    print(f"Wrote {a.out} ({len(pages)} page(s), uncompressed)")
    return 0


def cmd_icons(a):
    from adb import icons as I
    lib = Library()
    if a.icmd in ("resolve", "find"):
        if a.icmd == "find":
            key, _ = lib.lookup(a.name)
            if key and I.is_badge(lib.entries[key]):
                lib.retry_badges(only={I.normalize(lib.entries[key]["label"])})
            elif key and lib.badge_for(key, create=False):
                lib.retry_badges(only={I.normalize(lib.entries[f"name-{key}"]["label"])})
        # resolve only reports; find also searches online and saves what it finds
        r = lib.resolve(a.name, a.type, create=a.icmd == "find")
        print(json.dumps({"icon": r.icon_key, "technology": r.tech_key, "match": r.kind, "note": r.note}, indent=1))
    elif a.icmd == "list":
        for k, e in sorted(lib.entries.items(), key=lambda kv: (kv[1]["category"], kv[0])):
            if a.category and e["category"] != a.category:
                continue
            ok = lib.available(k)
            if a.missing and ok:
                continue
            print(f"{'ok ' if ok else '-- '} {e['category']:<11} {k:<28} {e['label']}  [{', '.join(e.get('aliases', [])[:4])}]")
    elif a.icmd == "fetch":
        only = [k.strip() for k in a.only.split(",")] if a.only else None
        _, _, failed = I.fetch(lib, only, a.category, a.force, a.prefer)
        if not only and not a.category:
            print("\nName tiles, searched online again:")
            found, still = lib.retry_badges()
            print(f"Found {len(found)}, still shown as names {len(still)}" + (f": {', '.join(still)}" if still else "."))
            if found:
                print("Run `apply-icons FILE --refresh` to put the new logos into existing diagrams.")
        return 1 if failed and only else 0
    elif a.icmd == "add":
        dest, clashes = I.add_icon(lib, a.key, a.file, a.url, a.label, [x.strip() for x in (a.aliases or "").split(",") if x.strip()],
                                   a.category, a.type, a.license, force=a.force)
        print(f"Added {a.key} -> {dest}")
        for c in clashes:
            print(f"  alias not added: {c}")
    elif a.icmd == "import":
        I.import_pack(lib, a.provider, a.dir, a.dry_run)
    elif a.icmd == "gallery":
        have, total = I.gallery(lib, a.out)
        print(f"Wrote {a.out} ({have}/{total} icons available)")
    elif a.icmd == "lint":
        problems, stray = I.lint(lib)
        for p in problems:
            print("PROBLEM:", p)
        for s in stray:
            print("not in manifest:", s)
        have = sum(1 for k in lib.entries if lib.available(k))
        print(f"{len(lib.entries)} entries, {have} with icon files, {len(problems)} problems, {len(stray)} stray files")
        return 1 if problems else 0
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--offline", action="store_true", help="never search online for missing logos")
    sub = p.add_subparsers(dest="cmd", required=True)
    targets = ["slide", "document", "screen"]
    b = sub.add_parser("build"); b.add_argument("spec"); b.add_argument("-o", "--out", required=True); b.add_argument("--preview")
    b.add_argument("--target", choices=targets); b.set_defaults(fn=cmd_build)
    i = sub.add_parser("inspect"); i.add_argument("file"); i.add_argument("--json", action="store_true"); i.add_argument("--page")
    i.set_defaults(fn=cmd_inspect)
    pa = sub.add_parser("patch"); pa.add_argument("file"); pa.add_argument("ops"); pa.add_argument("-o", "--out", required=True)
    pa.add_argument("--page"); pa.add_argument("--preview"); pa.add_argument("--target", choices=targets)
    pa.set_defaults(fn=cmd_patch)
    ai = sub.add_parser("apply-icons"); ai.add_argument("file"); ai.add_argument("-o", "--out", required=True)
    ai.add_argument("--page"); ai.add_argument("--map"); ai.add_argument("--dry-run", action="store_true")
    ai.add_argument("--refresh", action="store_true"); ai.set_defaults(fn=cmd_apply_icons)
    c = sub.add_parser("check"); c.add_argument("file"); c.add_argument("--spec"); c.add_argument("--compare"); c.add_argument("--page")
    c.add_argument("--target", choices=targets); c.add_argument("--covers"); c.add_argument("--covers-page")
    c.add_argument("--ignore"); c.set_defaults(fn=cmd_check)
    tl = sub.add_parser("tidy-lines"); tl.add_argument("file"); tl.add_argument("-o", "--out", required=True)
    tl.add_argument("--page"); tl.add_argument("--all-pages", action="store_true"); tl.set_defaults(fn=cmd_tidy_lines)
    ex = sub.add_parser("export"); ex.add_argument("file"); ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--page"); ex.add_argument("--target", choices=targets); ex.set_defaults(fn=cmd_export)
    pr = sub.add_parser("preview"); pr.add_argument("file"); pr.add_argument("-o", "--out", required=True); pr.add_argument("--page")
    pr.set_defaults(fn=cmd_preview)
    de = sub.add_parser("decode"); de.add_argument("file"); de.add_argument("out"); de.set_defaults(fn=cmd_decode)
    ic = sub.add_parser("icons"); isub = ic.add_subparsers(dest="icmd", required=True)
    r = isub.add_parser("resolve"); r.add_argument("name"); r.add_argument("--type")
    fi = isub.add_parser("find"); fi.add_argument("name"); fi.add_argument("--type")
    li = isub.add_parser("list"); li.add_argument("--category"); li.add_argument("--missing", action="store_true")
    f = isub.add_parser("fetch"); f.add_argument("--only"); f.add_argument("--category"); f.add_argument("--force", action="store_true")
    f.add_argument("--prefer", choices=["devicon", "simpleicons"], default="devicon")
    ad = isub.add_parser("add"); ad.add_argument("key"); ad.add_argument("--file"); ad.add_argument("--url"); ad.add_argument("--label")
    ad.add_argument("--aliases"); ad.add_argument("--category"); ad.add_argument("--type"); ad.add_argument("--license")
    ad.add_argument("--force", action="store_true")
    im = isub.add_parser("import"); im.add_argument("--provider", required=True); im.add_argument("--dir", required=True)
    im.add_argument("--dry-run", action="store_true")
    g = isub.add_parser("gallery"); g.add_argument("-o", "--out", required=True)
    isub.add_parser("lint")
    ic.set_defaults(fn=cmd_icons)
    a = p.parse_args(argv)
    if a.offline:
        os.environ["ADB_OFFLINE"] = "1"
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
