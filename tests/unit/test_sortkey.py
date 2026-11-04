"""The tree's order, stated as a rule: folders first, then natural names.

These are the cases the user feels: a folder stranded between two files, and
`b10` before `b2`. Each one is written down so a later "simplification" cannot
quietly bring the old order back.
"""

import unittest

from ostlr import sortkey


class TestFolderFirst(unittest.TestCase):
    def test_a_folder_sorts_before_a_file_whatever_its_name(self):
        self.assertEqual(["zzz", "aaa.txt"],
                         sortkey.sorted_names([("aaa.txt", False), ("zzz", True)]))

    def test_a_whole_mixed_directory(self):
        entries = [("b1.log", False), ("dist", True), ("AGENTS.md", False),
                   ("docs", True), (".git", True), ("README.md", False)]
        self.assertEqual([".git", "dist", "docs", "AGENTS.md", "b1.log", "README.md"],
                         sortkey.sorted_names(entries))


class TestNaturalNumbers(unittest.TestCase):
    def test_b2_comes_before_b10(self):
        self.assertEqual(["b2.log", "b10.log"],
                         sortkey.sorted_names([("b10.log", False), ("b2.log", False)]))

    def test_leading_zeros_compare_as_numbers_and_stay_stable(self):
        self.assertEqual(["v1.9", "v1.010"],
                         sortkey.sorted_names([("v1.010", False), ("v1.9", False)]))

    def test_a_long_list_of_chapters(self):
        names = ["ch%d.md" % n for n in (10, 2, 1, 100, 20)]
        self.assertEqual(["ch1.md", "ch2.md", "ch10.md", "ch20.md", "ch100.md"],
                         sortkey.sorted_names([(n, False) for n in names]))

    def test_text_and_numbers_do_not_crash_the_comparison(self):
        # ("a", text) vs (1, number): the tag keeps the kinds apart.
        self.assertEqual(["a", "a1", "ab"],
                         sortkey.sorted_names([("ab", False), ("a1", False), ("a", False)]))


class TestCaseRule(unittest.TestCase):
    def test_the_platform_decides_case_sensitivity(self):
        # Windows: two spellings of one name are adjacent, in a stable order.
        # Linux: they are two files and `sort` order is respected.
        key_a = sortkey.name_key("README")
        key_b = sortkey.name_key("readme")
        if sortkey.case_sensitive():
            self.assertNotEqual(key_a, key_b)
            self.assertLess(key_a, key_b)
        else:
            self.assertEqual(key_a, key_b)

    def test_cjk_names_are_ordered_and_stable(self):
        names = ["中文", "报告", "安卓"]
        first = sortkey.sorted_names([(n, False) for n in names])
        second = sortkey.sorted_names([(n, False) for n in reversed(names)])
        self.assertEqual(first, second)
        self.assertEqual(sorted(names), first)


if __name__ == "__main__":
    unittest.main()
