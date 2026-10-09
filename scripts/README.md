# Language territory generator

Generate `assets/voronoi-territory.svg` using Python 3.12 or newer:

```sh
python scripts/generate_territory.py --username TetewHeroez
```

There are no third-party dependencies. On Windows, use `py` if `python` is
unavailable. `GH_TOKEN` is optional locally and increases the GitHub API quota;
GitHub Actions uses its built-in `GITHUB_TOKEN`.

The generator paginates all public repositories owned by the user, excludes
forks and private repositories, and sums the language byte counts returned by
GitHub. Archived repositories are included. These are byte shares, not commit
counts or measures of proficiency. If there are more than nine languages, the
eight largest appear individually and the rest form `Other`.

Territories are a weighted Voronoi diagram (a power diagram). Polygon areas are
fitted to the language shares, with a relative tolerance of 0.1%, capped at 0.01
percentage points, and an absolute share tolerance of 0.000001 percentage points
for tiny regions. Lloyd relaxation moves sites towards their cell centroids to
reduce thin slivers. Small cells use the legend for labels. An account with no
language data gets an empty state.
API failures stop generation and preserve the previous SVG.

The workflow runs every six hours at approximately **01:17, 07:17, 13:17, and
19:17 WIB**, manually via **Actions → Update Voronoi Territory → Run workflow**,
or when generator/workflow changes are pushed to `main`. It commits only changed
SVG content. There is no timestamp, so identical data produces identical output.
The workflow needs permission to write repository contents and push to `main`.

For offline generation, provide a JSON object of language bytes:

```json
{"Python": 75000, "TeX": 25000}
```

```sh
python scripts/generate_territory.py --stats-file languages.json --output territory.svg
python -m unittest discover -s tests -v
```

API and workflow reference: [GitHub repository endpoints](https://docs.github.com/en/rest/repos/repos)
and [workflow triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

The 1200 × 420 canvas, left-hand map, right-hand legend, typography and palette
follow the [Voronoi Territory template](https://github.com/beydemirfurkan/awesome-github-profile/tree/main/templates/12-generative/voronoi-territory)
by beydemirfurkan, released under CC0. Language colours come from GitHub Linguist;
the cells and percentages are regenerated from this account's own statistics,
so their shapes differ from the template's sample data.
