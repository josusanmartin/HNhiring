import csv
import tempfile
import unittest
from pathlib import Path

import hn_who_is_hiring_chart as chart


class CategoryBehaviorTests(unittest.TestCase):
    def test_cryptography_is_not_a_public_category(self) -> None:
        self.assertNotIn("Cryptography", chart.CATEGORY_ORDER)

    def test_cryptography_terms_fall_through_to_other(self) -> None:
        rules = chart.compile_category_rules()

        category = chart.classify_comment(
            "Researching zero-knowledge cryptography and lattice schemes", rules
        )

        self.assertEqual(category, "Other")

    def test_legacy_cryptography_counts_are_merged_into_other(self) -> None:
        normalize = getattr(chart, "normalize_category_counts", None)
        self.assertIsNotNone(normalize)
        if normalize is None:
            return

        counts = normalize(
            {
                "AI/ML": 4,
                "Cryptography": 3,
                "Other": 5,
                "Unexpected Legacy Bucket": 2,
            }
        )

        self.assertNotIn("Cryptography", counts)
        self.assertEqual(counts["AI/ML"], 4)
        self.assertEqual(counts["Other"], 10)

    def test_category_order_uses_latest_share_with_other_pinned_top(self) -> None:
        order = getattr(chart, "category_order_for_latest_share", None)
        self.assertIsNotNone(order)
        if order is None:
            return

        rows = [
            {
                "month": "2026-05",
                "categories": {
                    "AI/ML": 10,
                    "Security": 20,
                    "Fintech": 30,
                    "Other": 900,
                },
            },
            {
                "month": "2026-06",
                "categories": {
                    "AI/ML": 35,
                    "Security": 50,
                    "Fintech": 5,
                    "Other": 70,
                },
            },
        ]

        ordered = order(rows)

        self.assertLess(ordered.index("Security"), ordered.index("AI/ML"))
        self.assertLess(ordered.index("AI/ML"), ordered.index("Fintech"))
        self.assertEqual(ordered[-1], "Other")

    def test_category_csv_omits_cryptography_and_merges_into_other(self) -> None:
        normalize = getattr(chart, "normalize_category_counts", None)
        self.assertIsNotNone(normalize)
        if normalize is None:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "categories.csv"
            chart.write_category_csv(
                [
                    {
                        "month": "2026-06",
                        "categories": normalize(
                            {"AI/ML": 4, "Cryptography": 3, "Other": 5}
                        ),
                    }
                ],
                str(path),
            )
            with path.open() as f:
                rows = list(csv.DictReader(f))

        categories = {row["category"]: int(row["value"]) for row in rows}
        self.assertNotIn("Cryptography", categories)
        self.assertEqual(categories["Other"], 8)


if __name__ == "__main__":
    unittest.main()
