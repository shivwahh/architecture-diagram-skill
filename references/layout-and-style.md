# Layout and visual standards

## What the layout engine does

- **Layers** become columns (`LR`) or rows (`TB`). Components in the same layer and boundary stack in that column.
- **Boundaries** are packed as nested blocks. A boundary's rectangle can only contain its own members, so containment on the page always matches the spec.
- **Ordering** within each column follows the average position of each component's neighbours (barycenter heuristic). Several orderings are tried and the one with the fewest crossings and bends wins. Components are then slid within their band to line up with their neighbours, which straightens most connectors.
- **Routing** is orthogonal. Connectors between neighbouring layers bend inside the gap between them, each in its own lane, ordered to avoid crossings. Connectors that skip layers travel along empty channels between rows and never along a boundary border. Connectors never pass through shapes; the build report says so if one does.
- **Labels** go at the clearest point along each connector, away from shapes, boundary headers, other labels and **other connectors' lines**. A label never sits where another line passes.
- **No overlapping lines.** Long connectors that run through the same stretch get separate channels even when they span different columns. Lanes in a gap are ordered so that one connector's end stub and another's start stub at (almost) the same height never lie on top of each other. Anything within 3 px counts as overlapping.
- **Crossings** that remain are drawn as U-bend line jumps (`jumpStyle=arc;jumpSize=8`): the connector later in the file hops over the earlier one. The preview draws the same hops.
- Sizes, gaps and positions snap to a 10 px grid; fonts, colours and spacing come from `assets/theme.json`.

## Choosing direction

- `LR` suits request/response systems and anything read like a pipeline. It's also best for slides, which are wide.
- `TB` suits layered views ("users on top, data at the bottom") and tall documents.
- If one layer has many components, the other direction usually spreads them better. Try both when in doubt; it's one rebuild.

## Visual standards (built in)

- One card size per diagram, consistent fonts (Helvetica 12 for names, 10 for technology), 10 px grid.
- Colour is purposeful only: white cards by default; status colours (new, changed, retiring, unchanged, to confirm) only when the spec sets `status`; boundaries in neutral greys with a dashed outline for logical groupings (clusters, networks, external).
- Solid arrows for requests and calls, dashed for events and async messages. Arrows point in the direction of the request or data.
- Every connector label says what moves and how, when the input says so.
- A legend appears automatically when status colours or both arrow styles are used.
- Brand icons keep their proportions and colours (`aspect=fixed`) and are never recoloured.

## Icon placement

- `card`: icon on the left, bold name, small technology line underneath. Best for system and container diagrams, longer names, and status colours. The default.
- `node`: the icon is the shape and the label sits below it. Best for technology-stack overviews with short names and mostly brand icons. Status colours appear as a tinted tile behind the logo. Connectors leaving a node downwards start below its label, so they never cut through text.
- `auto` picks `node` only when at least 70% of components resolve to brand icons, names are short, there are no statuses and there are 16 or fewer components; otherwise `card`.

## Common problems and fixes

| Symptom in the preview | Change in the spec |
|---|---|
| Many crossings into one shared component (monitoring, identity) | Keep only the flows that matter for this audience, or move that component to its own last layer. |
| A long connector snakes across the whole diagram | It skips several layers. Check the layer of both ends; maybe one belongs in a neighbouring layer. |
| A column is very tall while others are short | Split the layer (e.g. "Services" into "APIs" and "Workers"), or switch `direction`. |
| Components appear in an odd vertical order | List them in the spec in the order you'd read them; spec order seeds the layout. |
| A boundary is much wider than its contents | Its members span several layers. Check that each member really belongs in that boundary and layer. |
| Labels crowd one gap | Shorten labels to protocol or payload ("SQL", "OrderPlaced") and put detail in the document, not on the arrow. |
| A line runs through another arrow's text | Rebuild (the label placer avoids lines), or run `tidy-lines` on an existing diagram. |
| Crossings look like junctions | The connectors lack line jumps: run `tidy-lines` (adds `jumpStyle=arc`). |
| Two lines run on top of each other | On a built diagram: rebuild, since the router now separates them. Hand-made: reroute one connector by hand or with the `reroute` op. |
| Diagram too big for one slide | Split into two diagrams (context view and a zoomed-in view of one boundary) rather than shrinking text. |

The build report's routing line (`crossings, bends, overlapping segments, connectors through shapes`) is a quick quality measure. Aim for zero connectors through shapes and zero overlapping segments; a few crossings are normal for 15 or more flows.

## Readability for slides and pages

The Readability block (from `build`, or `check`/`patch`/`export --target`) answers one question: how big is the smallest text once the whole diagram is scaled to fit where it will be shown?
- Slide area: 1180 × 560 px (a 16:9 slide below its title). Page area: 600 × 820 px (portrait, inside margins). Edit `targets` in `assets/theme.json` for other formats.
- Text in pt = font px × fit scale × 0.75. Aim for 10 pt or more on slides (8 pt at least), and 8 pt or more on pages.
- "% filled" is box area over canvas area. Under about 15% means the canvas is mostly gaps and arrow channels.

Every fix below keeps every component. Readability is never bought by removing or merging architecture (see SKILL.md).

| Report says | Usual cause | Fix in the spec |
|---|---|---|
| too small, many boxes | a lot of architecture for one slide | let the builder search (drop `direction` and `icon_style`); shorten labels; merge layer rows that hold one box; then tell the user the size and ask (one slide at that size, full-slide diagram, or extra zoomed-in slides as well as the complete one) |
| too small, few boxes | many layers in one direction | let the builder try both directions (drop `direction`), merge layers |
| tall and narrow / very wide | aspect doesn't match 16:9 | switch `direction`; fewer layers or fewer boxes per layer |
| low fill | near-empty layers, long arrows skipping layers | put components where their requests arrive; merge layers holding one box |
| many words | detail lines and arrow labels | name plus 2–4 words; arrow labels 1–2 words; detail goes to speaker notes |

Boundaries side by side keep their spec order (e.g. Offline | Common | Online) unless swapping them saves more than one crossing, so list boundaries in reading order.

## Theme

`assets/theme.json` holds fonts, sizes, spacing, colours, status palette and boundary styles. If the user has a house style (corporate palette, font), change the theme rather than individual diagrams.
