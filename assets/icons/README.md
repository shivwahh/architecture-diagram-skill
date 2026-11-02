# Icon library

- `icon-manifest.json`: every known technology with its canonical key, aliases, category, generic type and icon path.
- `generic/`: original generic icons (database, queue, service, user...). Always available.
- Other folders fill up when you run `scripts/archdiagram.py icons fetch` (brand logos, needs internet) or
  `icons import --provider aws|azure|gcp --dir <unzipped official pack>` (cloud service icons).

Run `icons gallery -o gallery.html` to see everything, and `icons lint` to check the library.
See `references/icon-library.md` for matching rules, sources and licences.
