# Editing existing diagrams

Goal: make exactly the requested change and nothing else. Ids, styles and positions of everything you don't touch stay as they were.

## 1. Inspect

```
python scripts/archdiagram.py inspect diagram.drawio [--page NAME] [--json]
```

The output tells you:
- **Containers:** real draw.io containers (contents move with them) versus *plain boxes* (rectangles drawn around shapes). Membership of plain boxes is inferred from geometry.
- **Components:** id, label, position, which containers they're in, whether they have an icon, and any technology named in the label.
- **Connectors:** id, ends, label, solid or dashed.
- **Style profile:** typical component size, font, fill colours, connector style, and icon convention (`none`, `badges`, `icon-nodes`). New shapes copy this, so they look native.

Compressed files are read transparently. `decode in.drawio out.drawio` writes a readable copy if you need to look at the XML.

## 2. Operations

Write a JSON file `{"ops": [ ... ]}` (optionally `"page": "Name"`). Operations run in order; if one fails, nothing is written and the error says why.

| Operation | Fields | Notes |
|---|---|---|
| `add_component` | `id`, `label`, optional `tech`, `type`, `detail`, `status`, `boundary`, `near`, `side` (`right`/`below`/`left`/`above`), `like` (id of a shape to copy), `icon` (`auto`/true/false) | Placed next to `near` (or the boundary's members) where it overlaps nothing and is not inside any boundary it doesn't belong to. If the boundary is full, it grows, shifting shapes outside it if needed. Connectors that would run through the new shape are re-routed. Icons follow the diagram's convention unless `icon` says otherwise. |
| `add_flow` | `from`, `to`, optional `label`, `style` (`sync`/`async`), `bidirectional`, `id` | Routed around shapes and boundary labels, using a free side of each shape. Copies the diagram's connector style. |
| `remove` | `id`, optional `keep_children` (default true) | Removes the shape and every connector attached to it; the report lists them. Removing a container keeps its contents by default. |
| `relabel` | `id`, `label`, optional `detail` | Works on components, containers and connectors. Keeps the existing technology line unless `detail` is given. |
| `move_into` | `id`, `boundary`, optional `side` | Moves a component into a boundary and re-routes its connectors. |
| `add_boundary` | `id`, `label`, `kind`, `members` (ids), optional `parent` | Draws a container around existing shapes. Fails if the rectangle would also cover non-members (move those first). |
| `set_icon` | `id`, plus `tech`/`type` or an explicit `icon` key | Adds or replaces the icon; records the technology on the shape. |
| `set_status` | `id`, `status` | Colours a component; the legend gets the matching entry. |
| `update_flow` | `id`, optional `label`, `style`, `bidirectional` | Changes a connector without moving it. |
| `reroute` | `id` | Re-routes one connector around current shapes. |

Example:

```json
{"ops": [
  {"op": "add_component", "id": "notifier", "label": "Notification service", "tech": "Node.js",
   "type": "service", "boundary": "aks", "near": "worker", "status": "new"},
  {"op": "add_flow", "from": "events", "to": "notifier", "label": "consume", "style": "async"},
  {"op": "set_status", "id": "payment", "status": "changed"},
  {"op": "remove", "id": "legacy_esb"}
]}
```

Run it:

```
python scripts/archdiagram.py patch diagram.drawio ops.json -o diagram-v2.drawio --preview preview.png
```

## 3. Verify

The patch report compares old and new and lists every change: ADDED, REMOVED, RELABEL, MOVED INTO, RECONNECTED, REROUTED, REPOSITIONED, RESIZED, RESTYLED. Only issues the edit introduced are reported. Check that:
- every change is one you meant (look especially at REMOVED connectors and REPOSITIONED shapes)
- nothing important disappeared
- no new warnings remain

Then tell the user what changed in a few lines, including side effects such as "the legend moved down to make room".

## Tidying connector lines

```
python scripts/archdiagram.py tidy-lines diagram.drawio -o out.drawio [--page NAME | --all-pages]
```

- Every connector gets U-bend line jumps (`jumpStyle=arc;jumpSize=8`), so a crossing is a hop, not a junction.
- Every label that another line runs through, or that sits on a shape or another label, slides along its own connector to the clearest spot.
- Shapes and line routes don't move. The report gives "lines through labels before → after", and validation compares with the input.
- `patch` does the same automatically for the connectors it adds.

## Adding icons to an existing diagram

```
python scripts/archdiagram.py apply-icons diagram.drawio -o out.drawio --dry-run
python scripts/archdiagram.py apply-icons diagram.drawio -o out.drawio [--map order=python,db=postgresql]
```

Each component gets the icon for the technology in its label, or the one given with `--map`, as a small badge inside its box; nothing moves. Labels naming no technology are skipped, and labels naming two technologies are reported as ambiguous. Use `--refresh` after fetching icons to replace generic icons with brand icons.

## Editing XML by hand

For changes the operations don't cover (re-colouring a group, adding a note, changing page size), edit the XML following `drawio-notes.md`, then always run:

```
python scripts/archdiagram.py check new.drawio --compare old.drawio
```
