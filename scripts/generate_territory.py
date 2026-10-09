#!/usr/bin/env python3
"""Render public GitHub language bytes as a weighted Voronoi SVG.

Uses only Python's standard library. Run with --help for local/offline usage.
"""

import argparse
from collections import Counter
from html import escape
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MAX_TERRITORIES = 8
MAP_HEIGHT = 350 / 912
COLORS = {
    "Python": "#7aa2f7", "TeX": "#9ece6a", "JavaScript": "#e0af68",
    "TypeScript": "#7dcfff", "HTML": "#ff9e64", "CSS": "#bb9af7",
    "C++": "#f7768e", "C": "#a9b1d6", "Java": "#ff9e64",
    "Jupyter Notebook": "#e0af68", "Shell": "#73daca", "Other": "#7982a9",
}
PALETTE = ("#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7",
           "#f7768e", "#7dcfff", "#73daca", "#ff9e64")


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
    Coordinate bisection is deterministic and handles very unequal shares.
    """
    count = len(shares)
    if not count:
        return []
    if count == 1:
        return [[(0, 0), (1, 0), (1, MAP_HEIGHT), (0, MAP_HEIGHT)]]
    seeds = [(0.5 + 0.38 * math.cos(-0.9 + 2 * math.pi * i / count),
              MAP_HEIGHT * (0.5 + 0.38 * math.sin(-0.9 + 2 * math.pi * i / count)))
             for i in range(count)]
    weights = [0.0] * count
    for _ in range(300):
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
        if all(abs(area(polygon) / MAP_HEIGHT - target) < max(1e-8, target * 0.001)
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


def render_svg(username, totals, repository_count=None):
    entries = language_shares(totals)
    polygons = fit_territories([share for _, share in entries])
    owner = escape(username)
    summary = ", ".join(f"{escape(name)} {escape(percent(share))}" for name, share in entries)
    repo_label = "Public repositories" if repository_count is None else f"{repository_count} public repositories"
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="650" viewBox="0 0 960 650" role="img" aria-labelledby="title desc">',
        f'<title id="title">{owner} — Programming Language Territory</title>',
        f'<desc id="desc">Weighted Voronoi diagram of language bytes in public, non-fork repositories. {summary or "No language data available."}</desc>',
        '<defs><clipPath id="map"><rect x="24" y="112" width="912" height="350" rx="14"/></clipPath></defs>',
        '<style>text{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}.muted{fill:#a9b1d6}.label{fill:#c0caf5;font-size:14px}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}</style>',
        '<rect x="1" y="1" width="958" height="648" rx="22" fill="#1a1b26" stroke="#414868"/>',
        '<circle cx="32" cy="31" r="4" fill="#9ece6a"/>',
        '<text x="46" y="35" fill="#7aa2f7" font-size="11" letter-spacing="2" class="mono">LANGUAGE TERRITORY</text>',
        '<text x="24" y="77" fill="#c0caf5" font-size="29" font-weight="700">My coding territory</text>',
        f'<text x="24" y="98" class="muted" font-size="12">{owner} / {repo_label} / {len([v for v in totals.values() if v])} languages</text>',
        '<text x="934" y="35" text-anchor="end" class="muted mono" font-size="11">VORONOI / BYTE SHARE</text>',
        '<rect x="24" y="112" width="912" height="350" rx="14" fill="#24283b"/>',
        '<g clip-path="url(#map)">',
    ]
    for index, ((name, share), polygon) in enumerate(zip(entries, polygons)):
        color = COLORS.get(name, PALETTE[index % len(PALETTE)])
        points = " ".join(f"{24 + x * 912:.3f},{112 + y * 912:.3f}" for x, y in polygon)
        parts.append(f'<polygon points="{points}" fill="{color}" fill-opacity="0.22" stroke="{color}" stroke-width="1.5" stroke-linejoin="round"><title>{escape(name)}: {escape(percent(share))}</title></polygon>')
        # Keep small/narrow territories readable through the complete legend.
        if area(polygon) < 1e-12:
            continue
        cx, cy = centroid(polygon)
        label_width = max(len(name) * 8, 78) / 912
        label_height = 40 / 912
        corners = [(cx + dx * label_width / 2, cy + dy * label_height / 2)
                   for dx in (-1, 1) for dy in (-1, 1)]
        def inside(point):
            return all((q[0] - p[0]) * (point[1] - p[1])
                       - (q[1] - p[1]) * (point[0] - p[0]) >= -1e-10
                       for p, q in zip(polygon, polygon[1:] + polygon[:1]))
        if all(inside(point) for point in corners):
            x, y = 24 + cx * 912, 112 + cy * 912
            parts.extend([
                f'<text x="{x:.2f}" y="{y - 4:.2f}" text-anchor="middle" fill="{color}" font-size="15" font-weight="600">{escape(name)}</text>',
                f'<text x="{x:.2f}" y="{y + 17:.2f}" text-anchor="middle" fill="#c0caf5" font-size="18" class="mono">{escape(percent(share))}</text>',
            ])
    parts.append('</g>')
    if not entries:
        parts.append('<text x="480" y="290" text-anchor="middle" class="muted" font-size="18">No public language data yet</text>')
    for index, (name, share) in enumerate(entries):
        x, y = 32 + (index % 4) * 228, 497 + (index // 4) * 57
        color = COLORS.get(name, PALETTE[index % len(PALETTE)])
        parts.extend([
            f'<rect x="{x}" y="{y - 10}" width="8" height="8" rx="2" fill="{color}"/>',
            f'<text x="{x + 17}" y="{y}" class="label">{escape(name)}</text>',
            f'<text x="{x + 17}" y="{y + 21}" fill="{color}" font-size="14" class="mono">{escape(percent(share))}</text>',
        ])
    parts.extend([
        '<path d="M24 600H936" stroke="#414868"/>',
        '<text x="24" y="627" class="muted" font-size="11">Area follows code bytes · Public repos only · Forks excluded</text>',
        '<text x="936" y="627" text-anchor="end" class="muted mono" font-size="11">REFRESH / 6H</text>',
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
