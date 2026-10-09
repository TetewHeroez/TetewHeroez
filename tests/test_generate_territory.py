"""Check the byte accounting, API failure behavior, and weighted geometry."""

import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

from scripts import generate_territory as territory


class LanguageTests(unittest.TestCase):
    def test_top_languages_keep_all_bytes(self):
        totals = {f"Language {i}": i for i in range(1, 13)}
        entries = territory.language_shares(totals)
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries[0][0], "Language 12")
        self.assertEqual(entries[-1][0], "Other")
        self.assertAlmostEqual(entries[-1][1], sum(range(1, 6)) / sum(range(1, 13)))
        self.assertAlmostEqual(sum(share for _, share in entries), 1)

    def test_invalid_byte_counts_are_rejected(self):
        for totals in ([1], {"Python": -1}, {"Python": 1.5}, {"Python": True}):
            with self.subTest(totals=totals), self.assertRaises(ValueError):
                territory.language_shares(totals)
        self.assertEqual(territory.language_shares({"Python": 0}), [])

    def test_pagination_filters_forks_private_and_other_owners(self):
        def repo(name, **changes):
            return dict(full_name=f"Teo/{name}", owner={"login": "Teo"},
                        fork=False, private=False, **changes)

        public = repo("public")
        fork = {**repo("fork"), "fork": True}
        private = {**repo("private"), "private": True}
        outsider = {**repo("outside"), "owner": {"login": "someone-else"}}
        # 100 entries forces a second page; ignored repos must never be queried.
        first_page = [public, private, outsider] + [fork] * 97
        with patch.object(territory, "api_get", side_effect=[
            first_page, {"Python": 30, "TeX": 20}, [repo("archived", archived=True)],
            {"Python": 10},
        ]) as api:
            totals, count = territory.fetch_languages("teo", "test-token")
        self.assertEqual(totals, {"Python": 40, "TeX": 20})
        self.assertEqual(count, 2)
        self.assertIn("page=2", api.call_args_list[2].args[0])
        self.assertTrue(all(call.args[1] == "test-token" for call in api.call_args_list))

    def test_api_failure_does_not_return_partial_totals(self):
        repo = dict(full_name="Teo/code", owner={"login": "Teo"}, fork=False, private=False)
        with patch.object(territory, "api_get", side_effect=[[repo], RuntimeError("rate limit")]):
            with self.assertRaisesRegex(RuntimeError, "rate limit"):
                territory.fetch_languages("Teo")

    def test_auth_errors_fail_and_server_errors_retry(self):
        for status, expected_calls in ((403, 1), (503, 3)):
            error = HTTPError("https://api.github.com/test", status, "failure", {}, None)
            with patch.object(territory, "urlopen", side_effect=error) as request, \
                    patch.object(territory.time, "sleep"), self.assertRaises(RuntimeError):
                territory.api_get("/test")
            self.assertEqual(request.call_count, expected_calls)


class GeometryTests(unittest.TestCase):
    def test_areas_follow_language_shares_without_gaps(self):
        rng = random.Random(42)
        cases = [[1], [.5, .5], [.999999, .000001], [1/8] * 8,
                 [.99, .004, .002, .001, .001, .001, .0009, .0001]]
        for _ in range(15):
            sizes = [10 ** rng.uniform(-3, 3) for _ in range(rng.randint(2, 8))]
            cases.append([size / sum(sizes) for size in sizes])
        for shares in cases:
            with self.subTest(shares=shares):
                polygons = territory.fit_territories(shares)
                self.assertAlmostEqual(sum(map(territory.area, polygons)), territory.MAP_HEIGHT, places=8)
                for polygon, target in zip(polygons, shares):
                    self.assertAlmostEqual(territory.area(polygon) / territory.MAP_HEIGHT, target,
                                           delta=max(1e-8, target * .001))
                    for x, y in polygon:
                        self.assertTrue(-1e-9 <= x <= 1 + 1e-9)
                        self.assertTrue(-1e-9 <= y <= territory.MAP_HEIGHT + 1e-9)

    def test_svg_is_valid_escaped_and_deterministic(self):
        totals = {"C++ & <script>": 80, "Python": 20}
        svg = territory.render_svg('Teo & "Friends"', totals, 2)
        self.assertEqual(svg, territory.render_svg('Teo & "Friends"', totals, 2))
        root = ET.fromstring(svg)
        ns = {"s": "http://www.w3.org/2000/svg"}
        self.assertEqual(len(root.findall(".//s:polygon", ns)), 2)
        self.assertIn("C++ & <script>", root.find("s:desc", ns).text)
        self.assertEqual(root.findall(".//s:script", ns), [])
        self.assertNotIn("nan", svg.lower())

    def test_empty_account_has_an_honest_empty_state(self):
        svg = territory.render_svg("Teo", {}, 0)
        ET.fromstring(svg)
        self.assertIn("No public language data yet", svg)
        self.assertNotIn("<polygon", svg)

    def test_cli_keeps_existing_svg_on_failure_and_avoids_rewrites(self):
        with tempfile.TemporaryDirectory(dir=territory.ROOT) as directory:
            output = Path(directory) / "territory.svg"
            stats = Path(directory) / "stats.json"
            output.write_text("existing", encoding="utf-8")
            stats.write_text(json.dumps({"Python": -1}), encoding="utf-8")
            args = ["generate_territory.py", "--stats-file", str(stats), "--output", str(output)]
            with patch("sys.argv", args), patch("sys.stderr"), patch("builtins.print"):
                self.assertEqual(territory.main(), 1)
                self.assertEqual(output.read_text(encoding="utf-8"), "existing")
                stats.write_text(json.dumps({"Python": 100}), encoding="utf-8")
                self.assertEqual(territory.main(), 0)
                modified = output.stat().st_mtime_ns
                self.assertEqual(territory.main(), 0)
                self.assertEqual(output.stat().st_mtime_ns, modified)


if __name__ == "__main__":
    unittest.main()
