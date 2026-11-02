# Architecture spec format

The builder takes one JSON object. Only `components` is required; everything else has sensible defaults.

```json
{
  "title": "Online shop: target architecture",
  "subtitle": "Container level | Target state | As of [ADD: date]",
  "direction": "LR",
  "icon_style": "auto",
  "layers": ["Clients", "Edge", "Services", "Async & AI", "Data", "External"],
  "boundaries": [
    {"id": "azure", "label": "Azure (production subscription)", "kind": "azure"},
    {"id": "aks", "label": "AKS cluster", "kind": "kubernetes", "parent": "azure"}
  ],
  "components": [
    {"id": "order", "label": "Order service", "tech": "Python", "detail": "Python · FastAPI",
     "type": "service", "layer": "Services", "boundary": "aks", "status": "changed"}
  ],
  "flows": [
    {"from": "order", "to": "orders_db", "label": "SQL"},
    {"from": "order", "to": "events", "label": "OrderPlaced", "style": "async"}
  ]
}
```

A complete example is in `assets/examples/online-shop.json`.

## Top level

| Key | Default | Meaning |
|---|---|---|
| `title` | none | Diagram title. Make it state the point when the audience is non-technical. |
| `subtitle` | none | Level, scope, date. |
| `direction` | `LR` | `LR`: layers are columns, flow left to right. `TB`: layers are rows, flow top to bottom. |
| `icon_style` | `auto` | `card` (icon beside name), `node` (icon with label below), `none`, or `auto` (see SKILL.md). |
| `layers` | inferred | Ordered layer names. Components refer to them by name. Empty layers are dropped. |
| `show_layers` | true if `layers` given | Print layer names as column/row headers. |
| `legend` | `auto` | `auto` adds a legend when status colours or async flows are used. `true` / `false` force it. |
| `target` | `screen` | Where it will be shown: `slide` (16:9 PowerPoint), `document` (portrait page) or `screen`. Sets spacing and text sizes from `assets/theme.json` → `targets`. For `slide`/`document` without `direction`, both directions are tried and the more readable one is kept. Ask the user; see SKILL.md. |
| `show_title` | false for `slide`, else true | Draw `title`/`subtitle` on the canvas. Slides normally carry the title in the slide itself. |

## Layers

Choose layers from how the architecture actually flows, not from a template. Typical sets:

- Web/API systems: Clients → Edge → Services → Data → External
- Event-driven: Producers → Ingestion → Stream → Processing → Storage → Consumers
- AI/RAG: Users → App → Orchestration → Models & retrieval → Data sources
- Platform/DevOps: Source → Build → Registry → Deploy → Runtime

Rules of thumb:
- Put a component in the layer where requests reach it, so most flows go forward one layer.
- Shared infrastructure that everything talks to (monitoring, identity, secrets) works best as its own last layer, or shown with only the flows that matter. Ten arrows into "Monitoring" add noise, not information.
- Five to seven layers read well. If a layer holds more than about six components, consider splitting it.
- Without `layers`, components without `layer` are placed by flow direction (longest path). That's fine for quick drafts; explicit layers give better results.

## Components

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique id (letters, digits, `_`, `-`, `.`). Kept as the draw.io cell id, so later edits can target it. |
| `label` | yes | Name shown on the diagram ("Order service"). |
| `tech` | no | The technology it is, as precisely as the input says ("Azure Database for PostgreSQL", "Kafka", "Python"). Drives the icon and the small technology line. Leave it out rather than guess. |
| `type` | recommended | What kind of thing it is; picks the generic icon when the technology has no icon. See the list below. |
| `layer` | recommended | Layer name (or index). |
| `boundary` | no | Id of the innermost boundary the component lives in. |
| `detail` | no | Overrides the small second line (default: the technology's name, if it isn't already in the label). |
| `status` | no | `new`, `changed`, `retiring`, `unchanged`, `to-confirm`: colours the box and adds a legend entry. |

**Component types** (each maps to a generic icon): `user`, `users`, `web-app`, `mobile`, `desktop`, `service`, `api`, `gateway`, `load-balancer`, `function`, `container`, `cluster`, `server`, `database`, `cache`, `queue`, `stream`, `object-storage`, `file-storage`, `ai`, `external-system`, `security`, `identity`, `secrets`, `firewall`, `monitoring`, `logging`, `dashboard`, `cicd`, `repository`, `network`, `email`, `notification`, `scheduler`, `analytics`, `cloud`, `datacenter`, `device`, `etl`, `search`. Common synonyms work too (`microservice`, `db`, `broker`, `vm`, `cdn`, `llm`, `saas`...).

`tech` and `type` work together: `{"tech": "Redis", "type": "cache"}` shows the Redis logo when the library has it, and the generic cache icon until then.

## Boundaries

| Key | Meaning |
|---|---|
| `id`, `label` | Required. |
| `kind` | Visual style and default icon: `azure`, `aws`, `gcp`, `cloud`, `onprem`, `kubernetes`, `docker`, `network`, `environment`, `layer`, `external`, `generic`. |
| `parent` | Id of the enclosing boundary, for nesting (cloud → network → cluster). |
| `tech` | Optional technology for the boundary's icon (e.g. `"OpenShift"`). |

Boundaries become real draw.io containers: moving one in draw.io moves its contents. The layout never places a non-member inside a boundary's rectangle. Use boundaries for things that are true groupings (accounts, networks, clusters, environments, organisations), not for decoration.

## Flows

| Key | Meaning |
|---|---|
| `from`, `to` | Component ids (not boundaries). Direction is the direction of the request or data. |
| `label` | What moves and how, only as stated in the input: "REST", "SQL", "OrderPlaced events", "JDBC", "SFTP". Long labels wrap. |
| `style` | `sync` (solid, default) or `async` (dashed: events, messages, queues). |
| `bidirectional` | `true` draws arrowheads at both ends. Prefer two flows if the two directions carry different things. |
| `id` | Optional; defaults to `flow-<from>-<to>`. |

If the input says two things talk but not how, use an unlabelled flow rather than inventing a protocol.
