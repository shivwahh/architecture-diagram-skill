---
name: architecture-diagram-builder
description: >-
  Creates and edits native, fully editable draw.io (diagrams.net) architecture
  diagrams with the right technology icons from a local, reusable icon library.
  Use whenever the user wants an architecture, solution, system, cloud,
  deployment, infrastructure, integration or data-flow diagram drawn from a
  description, design document or spec, or wants an existing .drawio diagram
  changed: add, remove, rename, move or regroup components, connections or
  boundaries, add icons, or mark parts as new, changed or retiring. Also use when
  the user mentions draw.io, diagrams.net or .drawio files, asks for icons on a
  diagram (AWS, Azure, Google Cloud, Kubernetes, Docker, databases, DevOps
  tools), or wants to download, import or manage the icon library. Also use
  when a diagram must fit a PowerPoint slide or document page, is hard to
  read, or needs exporting as an image. Produces layered layouts, nested
  boundaries such as cloud, on-premises, Kubernetes cluster and network,
  labelled flows, legends, readability checks for slides and pages,
  validation, previews and slide-ready PNG exports.
---

# Architecture Diagram Builder

You do the architecture thinking; the bundled scripts do the geometry. Hand-written coordinates and XML are where diagrams usually go wrong (overlapping boxes, arrows through shapes, boxes that look like they sit in the wrong boundary), so describe the architecture as a small JSON spec and let `scripts/archdiagram.py` lay it out, pick icons, write native draw.io XML, validate it, and render a preview.

All commands below run as `python <skill-dir>/scripts/archdiagram.py ...` (Python 3.9+, standard library only). In claude.ai, write deliverables to `/mnt/user-data/outputs/`.

## Ground rules

These outrank aesthetics because a wrong architecture diagram does more harm than an ugly one.

- **Draw only what the input supports.** Every component, boundary, flow and protocol label must come from the user's description, document or existing diagram. If a relationship or protocol is unclear, leave it out or label it `[CONFIRM: ...]`, and say so in your reply. Never add "probably there" components such as a load balancer or a cache.
- **Never change meaning for looks.** Don't move a component into a different boundary, merge components, or reverse a flow to tidy the picture.
- **Readability never removes architecture.** When making a diagram simpler, fit a slide or redrawing it, every component of the original (and everything the user asked for) stays as its own box: no dropping, no folding several into one "Data stores" box, no moving a component into a note. Make it readable through layout and wording instead: shorter labels, a better direction, compact icon nodes, tighter spacing, fewer words on arrows. Dropping or merging anything needs the user's explicit OK, asked as a question, and the reply must list exactly what would go. For flows, keep every flow by default too. If you leave out a duplicate or trivial one, list it in your reply.
- **Icons follow the technology, never the vibe.** Identify the actual technology for each component, then let the library resolve it (aliases like "Postgres DB" → PostgreSQL). If a named technology has no icon file, the scripts search the free logo sets online (exact name only), save what they find in the library, and use it. If no logo exists anywhere, the library saves a grey tile showing the technology's name and uses that. Components with no named technology get a generic icon for their type. A component never gets another product's logo. Put the real technology in `tech` (e.g. "OPC UA", not "industrial protocol"), because whatever is there may end up printed on a name tile.
- **Lines stay readable.** No connector may run through another connector's label text, since the reader can't tell which line it belongs to. Avoid two connectors running on top of each other, which includes lines 1–3 px apart because they look like one line. Where a crossing is unavoidable, draw it as a **U-bend line jump** (`jumpStyle=arc;jumpSize=8`), so it can't be mistaken for a junction. `build` and `patch` do all of this. For any other diagram, run `tidy-lines`.
- **Boundaries mean membership.** A component inside the "AKS cluster" box must really run there. The layout engine guarantees non-members are never drawn inside a boundary; keep that guarantee when editing by hand.
- **People are roles.** Use "Platform team" or "Client PM", not personal names.
- If the stakeholder-content skill is also active, it decides audience, level of detail, title wording and what the diagram should say. This skill decides the file mechanics, and its rules win over that skill's draw.io notes.

## First use: check the icon library

Run `icons lint`. The last line shows how many icons have files. Only the 40 generic icons ship with the skill; brand logos (Docker, PostgreSQL, Kafka...) are downloaded once by `icons fetch`. Cloud-service icons (Lambda, AKS, BigQuery...) come from the providers' official packs via `icons import`.

Anything still missing is handled during diagram work: `build`, `patch` and `apply-icons` search online for a missing logo, store it, and fall back to a name tile when nothing exists. Without internet (or with `--offline` / `ADB_OFFLINE=1`), they go straight to name tiles. `icons fetch` later searches again for every name tile, and `apply-icons --refresh` puts the new logos into existing diagrams. The build report says which logos were downloaded and which components show name tiles; pass that on to the user. Details: `references/icon-library.md`.

## Before any new diagram or change: ask where it will be shown

A diagram that reads well zoomed in on screen can shrink to 3 pt text on a slide. So before you build or change anything, ask the user (with the question tool) whether the diagram will go into a PowerPoint slide, a document page, or stay on screen. Skip the question only if they've already said so in this conversation, or the spec already has a `target`. Offer:
- **PowerPoint slide (16:9)** → `"target": "slide"`
- **Document page (Word, PDF, Confluence)** → `"target": "document"`
- **Screen only (draw.io, wiki, zoomable)** → `"target": "screen"`, today's behaviour

Then design for that answer:

| | Slide | Document page | Screen |
|---|---|---|---|
| Components | **all of them**, each its own box | all | all |
| Text per box | name plus 2–4 words | name plus a short line | name plus a short line |
| Arrow labels | 1–2 words, or none | 1–4 words | short |
| Title on the canvas | no (the slide has one) | yes | yes |
| Detail | speaker notes or a backup slide | text next to the figure | tooltips or a notes area |
| Goal in the report | smallest text 10 pt or more (8 pt at least) | 8 pt or more | none |

The content is fixed; the layout is what you adjust. `build` reads `target` from the spec (or `--target`). For a slide or page, it builds **every layout the spec leaves open** (direction LR and TB, icon style card and node) with all components, keeps the one whose smallest text stays largest, and prints what it tried. Leave `direction` and `icon_style` out of the spec so it can search. Then it prints a **Readability** block: size, boxes, arrows, words, how much of the canvas is filled, and the smallest text in points once fitted. `check`, `patch` and `export` take `--target` for the same report on any diagram, including hand-made ones.

If the best layout is still below the goal, improve it without losing anything:
- shorten names and detail lines
- drop words from arrow labels
- merge layer rows that hold one box each (rows are presentation, not architecture)
- pass on how big the text will be

Then **ask** the user how to proceed, with options such as:
- keep everything on one slide at that size
- use the whole slide for the diagram (title in the notes)
- add zoomed-in slides as well as the complete one, never instead of it

A zoomed-in slide must still contain every component inside its scope. For example, a plant slide shows everything that runs on the plant, including Object Store, MLflow and login.

**Whenever the new diagram is derived from an existing one** (a slide version, a simplified redraw, a zoomed-in view), run `check new.drawio --covers original.drawio [--covers-page NAME]`. It lists every original component without a counterpart. Fix all of them before delivering. For a zoomed-in view, pass the out-of-scope components with `--ignore` and say so. On hand-made originals it also lists headers and notes; `--ignore` those, but read the list first.

**Delivering for PowerPoint:** `export diagram.drawio -o slide.png --target slide` writes a 1920×1080 PNG with the diagram fitted and centred, ready to insert. It uses the draw.io desktop app when installed (exact output), otherwise the built-in renderer through Edge/Chrome, which is close but not exact; say which one was used. Also hand over the `.drawio` so they can edit it.

## Workflow A: new diagram

1. **Understand the architecture first.** From the input, list:
   - users and clients
   - applications, services and APIs
   - data stores, storage, queues and streams
   - AI/ML services
   - security, monitoring and CI/CD
   - external systems
   - boundaries: cloud accounts, on-premises, clusters, networks, environments
   - the flows between components, with protocols where they're given

   Decide the layers (the left-to-right or top-to-bottom bands) from how requests and data actually move. Don't force a fixed template. If something material is ambiguous (which boundary a service runs in, which way data flows), ask before drawing, or draw only the certain part and list the open questions.
2. **Write the spec** following `references/spec-format.md`: `layers`, `boundaries`, `components` (id, label, tech, type, layer, boundary, optional status/detail) and `flows` (from, to, label, sync/async). Start from `assets/examples/online-shop.json`.
3. **Build:** `build spec.json -o <architecture-name>.drawio --preview preview.png`, with `"target"` set from the user's answer.
4. **Read the report.** Fix any spec problems or validation errors. Look at the icon notes: generic fallbacks are fine, but check each recognised technology is the right one. Check the Readability block. If it says "too small" or "tight", or less than about 15% of the canvas is filled, change the spec (group components, drop duplicate flows, shorten labels, merge near-empty layers) and rebuild, rather than delivering it.
5. **Look at the preview** (`preview.png`; it falls back to SVG if no PNG converter exists). If it's cluttered, change the spec rather than the XML. Typical fixes:
   - reassign a component's layer
   - split a crowded layer
   - reorder components (spec order seeds the vertical order)
   - switch `direction`
   - drop flows that duplicate others

   Then rebuild. `references/layout-and-style.md` lists common problems and fixes.
6. **Deliver** the `.drawio` file. Add a few lines covering assumptions made, `[CONFIRM]` placeholders, logos that were downloaded during the build, and components showing name tiles (`icons find "NAME"` searches again, `icons add KEY --file logo.svg` adds the user's own file).

## Workflow B: change an existing diagram

1. **Inspect:** `inspect existing.drawio`. This shows components, containers (true draw.io containers vs plain rectangles drawn around things), connectors, the style profile and the icon convention. Use `--json` for exact data and `--page NAME` for multi-page files.
2. **Plan the smallest change** that does what was asked, as operations in a JSON file (`references/editing.md`): `add_component`, `add_flow`, `remove`, `relabel`, `move_into`, `add_boundary`, `set_icon`, `set_status`, `update_flow`, `reroute`. New shapes copy the diagram's own style and are placed where they overlap nothing and don't land in a boundary they don't belong to. If there's no room, the boundary grows and outside shapes shift to make space.
3. **Apply:** `patch existing.drawio ops.json -o updated.drawio --preview preview.png --target <answer>`. The report lists every change (ADDED, REMOVED, RELABEL, REROUTED, REPOSITIONED, RESIZED, RESTYLED) and only the issues the change introduced, plus the Readability block. If the user wants an existing diagram on a slide and the report says it won't fit, a patch can't fix that. Offer a slide version built fresh from it on a new page, and leave the original unchanged:
1. Take every component from `inspect --json`.
2. Build with `"target": "slide"`.
3. Prove it's complete with `check --covers`.
4. **Verify the change list matches the request.** Watch for connectors removed along with a component; mention them to the user. Connectors that `patch` adds get U-bend line jumps, and any label they would run through is moved along its own line.
5. **Tidy the lines of an existing diagram** (hand-made, or built before line jumps existed): `tidy-lines in.drawio -o out.drawio --page NAME` (or `--all-pages`). It adds U-bend line jumps to every connector, and slides any label that a line runs through to a clear spot on its own connector. No shape or line moves. Afterwards check must report no "runs through the label" warnings. If it reports "on top of each other", those lines need rerouting: on a skill-built page, offer a rebuild; on a hand-made page, point the user to the connectors involved.
6. **To add icons** to a diagram that has none: `apply-icons in.drawio -o out.drawio --dry-run` first, then without `--dry-run`. Labels that name no technology are skipped. Use `--map id=tech` where the label doesn't say. After fetching new icons, `--refresh` upgrades generic icons in diagrams this skill built.

Rebuild an existing diagram from scratch only when the user asks for a redraw. Then derive the spec from `inspect --json`, and warn that positions and ids will change.

For changes the operations don't cover, edit the XML directly using `references/drawio-notes.md`, then run `check new.drawio --compare old.drawio`.

## How icons are placed ("the skill decides")

- **New diagrams (`icon_style: auto`):** cards with the icon left of the name and a small technology line (`card`), which suit system and container diagrams, status colours and longer names. The builder switches to icon-first nodes with the label underneath (`node`) for technology-stack overviews: mostly brand icons, short names, no status colours, 16 components or fewer. Set `icon_style` explicitly to override.
- **Existing diagrams:** follow their convention. Diagrams without icons get none unless the user asks, and then `apply-icons` adds small badges inside the existing boxes without moving anything.

## Validation

`build` and `patch` run `check` automatically. It reports:
- XML problems, duplicate or dangling ids
- overlapping shapes, and shapes drawn inside a container they don't belong to
- connectors attached to missing shapes or passing through shapes
- connectors running through another connector's label, and connectors running on top of each other (within 3 px)
- crossings, and whether they're drawn as U-bend line jumps
- labels that are too small, overflow their box, or have low contrast
- icons that don't match their component's technology
- locked or flattened content
- (`--spec`) components and flows missing from the spec
- (`--compare`) everything that changed

It can't run draw.io itself. The preview is a close approximation, so for an important deliverable suggest the user glance at it in draw.io.

## Reference files

| File | Read when |
|---|---|
| `references/spec-format.md` | writing a spec for a new diagram |
| `references/layout-and-style.md` | choosing layers/direction, fixing a cluttered layout, visual standards |
| `references/editing.md` | changing an existing diagram |
| `references/icon-library.md` | icon matching, fetching, importing official packs, adding icons, licences |
| `references/drawio-notes.md` | editing draw.io XML by hand |
