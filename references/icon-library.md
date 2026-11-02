# Icon library

Everything lives in `assets/icons/`:

```
assets/icons/
├── icon-manifest.json   canonical names, aliases, categories, file paths, sources
├── generic/             40 original generic icons (always present)
├── cloud/               provider logos; cloud/aws, cloud/azure, cloud/gcp hold service icons from official packs
├── databases/ containers/ devops/ languages/ ai-ml/ messaging/
├── monitoring/ security/ storage/ networking/ saas/
├── found/               logos found online for technologies the manifest didn't know
├── names/               name tiles for technologies with no logo anywhere
```

Icons are embedded into each `.drawio` file as SVG data, so diagrams display offline, in Confluence, and on anyone's machine, with no links to break.

## Manifest entries

```json
"postgresql": {
  "label": "PostgreSQL",
  "aliases": ["postgres", "psql", "pg", "pgsql", "postgres db"],
  "category": "databases",
  "type": "database",
  "icon": "databases/postgresql.svg",
  "sources": {"devicon": ["postgresql"], "simpleicons": ["postgresql"]},
  "source_url": "...", "license": "...", "retrieved": "2026-11-01"
}
```

- `type` is the generic fallback for the technology when its file is missing.
- `fallback` (optional) names a same-technology icon to use when this one is missing, e.g. `azure-postgresql` → `postgresql`. Fallbacks never point to a different product.
- Cloud services have `pack` (`aws` / `azure` / `gcp`) and `pack_names`: how the service is named in the provider's official icon files.
- `source_url`, `license` and `retrieved` are filled in automatically when an icon is fetched, imported or added.

## How a technology is matched

1. Normalise the name: case, punctuation, dots (`Node.js` = `nodejs`).
2. Exact match on the canonical key, the label or an alias.
3. Otherwise strip version numbers (`PostgreSQL 15`), then vendor prefixes (`Azure`, `Amazon`, `AWS`, `Google`, `Apache`, `managed`...), then trailing generic words (`db`, `cluster`, `service`, `cache`, `container`...) and try again. `Postgres DB`, `Redis cache` and `Docker container` resolve this way.
4. No fuzzy or partial matching: "data flow" will not become Google Dataflow, and "container" will not become Docker.
5. If the technology is recognised but its file is missing: download it (its own `sources`, then the online search below), then its `fallback` chain, then a name tile.
6. If the technology is unknown: the online search below; if that finds nothing, a name tile. A `tech` that is really a component type ("database", "auth", "users") gets the generic icon instead.
7. With no technology named: the generic icon for the component's `type`. With no type either, no icon.

Test any name with `icons resolve "Azure Cache for Redis" --type cache`. `resolve` only reports and never downloads. `icons find "NAME"` runs the full lookup and saves the result, and searches again if the name currently shows as a tile.

## Online search and name tiles

The search uses the Iconify API (api.iconify.design, no key needed) and looks only in sets of product logos with permissive licences: Devicon (MIT), SVG Logos (CC0), Skill Icons (MIT), theSVG (MIT) and Simple Icons (CC0). General UI icon sets are never searched, so "Gateway" can't turn into some unrelated glyph.

Matching is strict. An icon counts only when its own name equals the technology name, without spaces, version numbers or trailing words like "db" or "server" (`TimescaleDB` finds `timescale`; `OPC UA` tries `opcua`). Near matches are refused: "DCS" will not take an icon called "dcss". Vendor prefixes are kept, so "Azure Functions" never matches a plain "functions" icon.

- Logos found for unknown technologies are saved under `found/` as new entries. Logos for known entries go to the entry's own path. Each records its `source_url` and `license`.
- Name tiles are saved under `names/` as `name-<slug>` entries (`"generated": "name-badge"`). They are neutral grey with dark text, so they can't be mistaken for a brand. A tile is reused on later runs without going online again.
- `icons fetch` searches again for every tile. When a logo turns up, the tile is marked `badge_for` the new entry, and `apply-icons --refresh` swaps it in existing diagrams. Diagrams that still show the old tile keep passing `check`.
- After the first network failure, a run stops trying and uses tiles for the rest of that run.

## Getting the brand icons (internet is secondary)

The library is always used first. Diagram work only goes online for a logo the library doesn't have (see "Online search and name tiles"), and `--offline` stops even that.

**Developer tools and products:** `icons fetch` downloads every missing icon listed in the manifest. It tries Devicon (MIT licence, multi-colour) first, then Simple Icons (CC0, brand colour). Icons that already exist are skipped unless `--force` is given. Use `--only docker,kafka` or `--category databases` to limit it, and `--prefer simpleicons` for a flatter, single-colour look. This needs internet access: run it from Claude Code (or any terminal) on a machine that has it. A sandbox without network access will report every icon as failed. Failures are listed and those components keep their generic icons.

**Cloud services:** download the official packs once from the providers (they require accepting their terms on the website, so this is a manual step):
- AWS Architecture Icons (aws.amazon.com/architecture/icons)
- Azure architecture icons (Microsoft Learn: "Azure icons")
- Google Cloud icons (cloud.google.com/icons)

Unzip the pack you downloaded (use its real file name, e.g. `Asset-Package_<date>.zip`; some packs contain further zips, so unzip those too), then: `icons import --provider aws --dir path/to/unzipped --dry-run`, check the matches, and run again without `--dry-run`. Official files replace any logo the online search found earlier. Curated entries (S3, Lambda, AKS, BigQuery...) pick up the official files. Every other icon in the pack is added as a new entry named after its file (e.g. `aws-app-runner`), so the whole pack becomes usable.

**Anything else** (internal systems, a vendor logo from the vendor's media kit):
`icons add acme-billing --file logo.svg --label "Acme Billing" --aliases "billing engine" --category saas --type external-system`.
`--url` works where there is internet access. Aliases that already belong to another icon are refused, so names stay unambiguous.

After changes, `icons lint` checks for alias collisions, broken SVGs and stray files, and `icons gallery -o gallery.html` builds a page showing every icon, its aliases and its source. Use the gallery to eyeball fetched logos once.

## Making the library available everywhere

The library lives inside the skill folder. After fetching or importing (e.g. in Claude Code at `~/.claude/skills/architecture-diagram-builder/`), copy or re-zip that folder to use the same icons in other tools, such as claude.ai (upload the zipped folder as a skill) or another agent that reads skills.

## Licences and trademarks

- The generic icons are original to this skill and free to use.
- Devicon is MIT-licensed and Simple Icons is CC0, but the logos remain trademarks of their owners. Using a logo to show which product sits where in an architecture diagram is standard practice; don't recolour, distort or combine logos. Some companies' brand guidelines also ask for clear space or a minimum size.
- AWS, Microsoft and Google publish their architecture icons for exactly this purpose, under their own terms; the manifest records the source of each imported file.
- `license` and `source_url` in the manifest record where every icon came from.
