#!/usr/bin/env python3
"""Render public GitHub language bytes as a weighted Voronoi SVG.

Uses only Python's standard library. Run with --help for local/offline usage.
"""

import argparse
from collections import Counter
from html import escape
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MAX_TERRITORIES = 9
MAP_X = 16
MAP_Y = 16
MAP_WIDTH = 700
MAP_HEIGHT = 264 / MAP_WIDTH
LEGEND_X = MAP_X + MAP_WIDTH + 32
LEGEND_RIGHT = LEGEND_X + 292
SVG_WIDTH = LEGEND_RIGHT + 16
SVG_HEIGHT = 296
# GitHub Linguist colours; the layout follows the CC0 Voronoi Territory template.
COLORS = {
    "Python": "#3572a5", "TeX": "#3D6117", "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6", "HTML": "#e34c26", "CSS": "#663399",
    "C++": "#f34b7d", "C": "#555555", "Java": "#b07219",
    "Jupyter Notebook": "#DA5B0B", "Shell": "#89e051", "Other": "#7c86b8",
    "PostScript": "#da291c", "Rust": "#dea584", "Go": "#00ADD8",
    "Nix": "#7e7eff", "R": "#198CE7", "Ruby": "#701516",
}
PALETTE = ("#3178c6", "#663399", "#f1e05a", "#dea584", "#3572a5",
           "#00add8", "#89e051", "#e34c26", "#7e7eff")


def api_get(path, token=""):
    """Fail on incomplete API data; retry only transient server/network errors."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "github-profile-territory"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com{path}", headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            hint = " Check GH_TOKEN or the API rate limit." if error.code in (401, 403, 429) else ""
            raise RuntimeError(f"GitHub API returned HTTP {error.code} for {path}.{hint}") from error
        except (URLError, TimeoutError) as error:
            if attempt == 2:
                raise RuntimeError(f"Cannot reach GitHub API for {path}: {error}") from error
            time.sleep(2 ** attempt)


def fetch_languages(username, token=""):
    totals = Counter()
    repository_count = 0
    page = 1
    while True:
        repositories = api_get(
            f"/users/{quote(username, safe='')}/repos?type=owner&sort=full_name&per_page=100&page={page}",
            token,
        )
        for repo in repositories:
            if (repo["fork"] or repo["private"]
                    or repo["owner"]["login"].casefold() != username.casefold()):
                continue
            # Archived repositories still represent the owner's public code.
            full_name = quote(repo["full_name"], safe="/")
            totals.update(api_get(f"/repos/{full_name}/languages", token))
            repository_count += 1
        if len(repositories) < 100:
            return dict(totals), repository_count
        page += 1


def language_shares(totals):
    if not isinstance(totals, dict) or any(
        not isinstance(name, str) or not name
        or type(size) is not int or size < 0 for name, size in totals.items()
    ):
        raise ValueError("Language statistics must be an object of language names and nonnegative integer bytes.")
    entries = sorted(((name, size) for name, size in totals.items() if size),
                     key=lambda entry: (-entry[1], entry[0]))
    if len(entries) > MAX_TERRITORIES:
        entries = entries[:MAX_TERRITORIES - 1] + [
            ("Other", sum(size for _, size in entries[MAX_TERRITORIES - 1:]))
        ]
    total = sum(totals.values())
    return [(name, size / total) for name, size in entries]


def clip_polygon(polygon, a, b, c):
    """Intersect a convex polygon with the half-plane a*x + b*y <= c."""
    result = []
    if not polygon:
        return result
    previous = polygon[-1]
    previous_distance = a * previous[0] + b * previous[1] - c
    for current in polygon:
        distance = a * current[0] + b * current[1] - c
        if (distance <= 0) != (previous_distance <= 0):
            fraction = previous_distance / (previous_distance - distance)
            result.append((previous[0] + fraction * (current[0] - previous[0]),
                           previous[1] + fraction * (current[1] - previous[1])))
        if distance <= 0:
            result.append(current)
        previous, previous_distance = current, distance
    return result


def cell(index, seeds, weights):
    polygon = [(0, 0), (1, 0), (1, MAP_HEIGHT), (0, MAP_HEIGHT)]
    x, y = seeds[index]
    for other, (u, v) in enumerate(seeds):
        if other != index:
            polygon = clip_polygon(polygon, 2 * (u - x), 2 * (v - y),
                                   u*u + v*v - x*x - y*y + weights[index] - weights[other])
    return polygon


def area(polygon):
    return abs(sum(x * v - u * y for (x, y), (u, v)
                   in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def fit_territories(shares):
    """Fit power-diagram weights so polygon areas match byte shares.

    Each cell uses squared distance minus an additive weight (weighted Voronoi).
    Coordinate bisection fits areas; Lloyd steps keep the cells compact.
    """
    count = len(shares)
    if not count:
        return []
    if count == 1:
        return [[(0, 0), (1, 0), (1, MAP_HEIGHT), (0, MAP_HEIGHT)]]
    # Staggered sites mirror the template's composition, with the largest cell
    # near the centre and smaller cells distributed around it.
    positions = [(0.405, 0.561), (0.870, 0.596), (0.691, 0.246),
                 (0.116, 0.713), (0.076, 0.263), (0.668, 0.789),
                 (0.200, 0.290), (0.420, 0.090), (0.930, 0.120)]
    seeds = [(x, y * MAP_HEIGHT) for x, y in positions[:count]]
    weights = [0.0] * count
    for iteration in range(360):
        for index, target in enumerate(shares):
            low, high = min(weights) - 2, max(weights) + 2
            for _ in range(32):
                weights[index] = (low + high) / 2
                actual = area(cell(index, seeds, weights)) / MAP_HEIGHT
                if actual < target:
                    low = weights[index]
                else:
                    high = weights[index]
        offset = weights[-1]
        weights = [weight - offset for weight in weights]
        polygons = [cell(i, seeds, weights) for i in range(count)]
        # Relax early, then finish with fixed sites to recover precise areas.
        if iteration < 60:
            if iteration % 3 == 2:
                for index, polygon in enumerate(polygons):
                    if area(polygon) > 1e-12:
                        cx, cy = centroid(polygon)
                        x, y = seeds[index]
                        seeds[index] = (x + (cx - x) * 0.25, y + (cy - y) * 0.25)
            continue
        if all(abs(area(polygon) / MAP_HEIGHT - target) < max(1e-8, min(0.0001, target * 0.001))
               for polygon, target in zip(polygons, shares)):
            return polygons
    raise RuntimeError("Weighted Voronoi layout did not converge; existing SVG was preserved.")


def centroid(polygon):
    crosses = [x * v - u * y for (x, y), (u, v)
               in zip(polygon, polygon[1:] + polygon[:1])]
    scale = 3 * sum(crosses)
    return tuple(sum((p[axis] + q[axis]) * cross
                     for p, q, cross in zip(polygon, polygon[1:] + polygon[:1], crosses)) / scale
                 for axis in (0, 1))


def percent(share):
    return "<0.1%" if share < 0.001 else f"{share:.1%}"


def label_ink(color):
    """Choose readable ink against the cell colour composited on the ground."""
    ground = (11, 15, 22)
    channels = [(int(color[i:i + 2], 16) * .85 + background * .15) / 255
                for i, background in zip((1, 3, 5), ground)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
              for value in channels]
    luminance = sum(value * factor for value, factor in zip(linear, (.2126, .7152, .0722)))
    return "#0b0f16" if luminance > .18 else "#f4f7ff"


def render_svg(username, totals, repository_count=None):
    entries = language_shares(totals)
    polygons = fit_territories([share for _, share in entries])
    owner = escape(username)
    summary = ", ".join(f"{escape(name)} {escape(percent(share))}" for name, share in entries)
    repo_label = "Public repositories" if repository_count is None else f"{repository_count} public repositories"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{owner} — Programming languages</title>',
        f'<desc id="desc">Weighted Voronoi diagram of language bytes. {repo_label}, forks excluded. {summary or "No language data available."}</desc>',
        '<!-- Layout adapted from beydemirfurkan/awesome-github-profile: Voronoi Territory (CC0). -->',
        '<style>text{font-family:ui-monospace,Menlo,Consolas,monospace}</style>',
        f'<rect width="{SVG_WIDTH}" height="{SVG_HEIGHT}" fill="#0b0f16"/>',
        '<g id="territories">',
    ]
    for index, ((name, share), polygon) in enumerate(zip(entries, polygons)):
        color = COLORS.get(name, PALETTE[index % len(PALETTE)])
        points = " ".join(f"{MAP_X + x * MAP_WIDTH:.3f},{MAP_Y + y * MAP_WIDTH:.3f}" for x, y in polygon)
        parts.append(f'<polygon points="{points}" fill="{color}" fill-opacity="0.85" stroke="#0b0f16" stroke-width="2"><title>{escape(name)}: {escape(percent(share))}</title></polygon>')
        # Keep small/narrow territories readable through the complete legend.
        if area(polygon) < 1e-12:
            continue
        cx, cy = centroid(polygon)
        label_width = max(len(name) * 7.5, 52) / MAP_WIDTH
        label_height = 36 / MAP_WIDTH
        corners = [(cx + dx * label_width / 2, cy + dy * label_height / 2)
                   for dx in (-1, 1) for dy in (-1, 1)]
        def inside(point):
            return all((q[0] - p[0]) * (point[1] - p[1])
                       - (q[1] - p[1]) * (point[0] - p[0]) >= -1e-10
                       for p, q in zip(polygon, polygon[1:] + polygon[:1]))
        if share >= .06 and all(inside(point) for point in corners):
            x, y = MAP_X + cx * MAP_WIDTH, MAP_Y + cy * MAP_WIDTH
            ink = label_ink(color)
            parts.extend([
                f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle" fill="{ink}" font-size="12" font-weight="700">{escape(name)}</text>',
                f'<text x="{x:.2f}" y="{y + 17:.2f}" text-anchor="middle" fill="{ink}" font-size="11" opacity="0.7">{escape(percent(share))}</text>',
            ])
    parts.append('</g>')
    if not entries:
        parts.append(f'<text x="{MAP_X + MAP_WIDTH / 2:g}" y="{MAP_Y + MAP_HEIGHT * MAP_WIDTH / 2:g}" text-anchor="middle" fill="#7c86b8" font-size="14">No public language data yet</text>')
    parts.extend([
        '<g id="legend">',
        f'<text x="{LEGEND_X}" y="24" font-size="10" fill="#7c86b8" letter-spacing="4">BY AREA</text>',
        f'<line x1="{LEGEND_X}" y1="36" x2="{LEGEND_RIGHT}" y2="36" stroke="#1e2637"/>',
    ])
    for index, (name, share) in enumerate(entries):
        y = 58 + index * 28
        color = COLORS.get(name, PALETTE[index % len(PALETTE)])
        parts.extend([
            f'<rect x="{LEGEND_X}" y="{y - 10}" width="12" height="12" rx="2" fill="{color}"/>',
            f'<text x="{LEGEND_X + 24}" y="{y}" font-size="12" fill="#c8d2e8">{escape(name)}</text>',
            f'<text x="{LEGEND_RIGHT}" y="{y}" text-anchor="end" font-size="12" fill="#7c86b8">{escape(percent(share))}</text>',
        ])
    parts.extend([
        '</g>',
        '</svg>',
    ])
    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=os.getenv("GITHUB_REPOSITORY_OWNER", "TetewHeroez"))
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "voronoi-territory.svg")
    parser.add_argument("--stats-file", type=Path, help="Offline JSON object mapping language names to byte counts")
    args = parser.parse_args()
    try:
        if args.stats_file:
            totals = json.loads(args.stats_file.read_text(encoding="utf-8-sig"))
            count = None
        else:
            totals, count = fetch_languages(args.username, os.getenv("GH_TOKEN", ""))
        svg = render_svg(args.username, totals, count)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists() and args.output.read_text(encoding="utf-8") == svg:
            print(f"Unchanged: {args.output}")
        else:
            temporary = args.output.with_suffix(".svg.tmp")
            temporary.write_text(svg, encoding="utf-8", newline="\n")
            temporary.replace(args.output)
            print(f"Generated: {args.output}")
        print(f"Languages: {len(totals)}; code bytes: {sum(totals.values()):,}")
    except (RuntimeError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
