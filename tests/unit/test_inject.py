"""What a path looks like after it is sent: the one text both readers see.

The rule has to survive two readers at once - the shell or agent that receives
the string, and the person reading the receipt - so it is a pure function with
its cases written down here.
"""

import unittest

from ostlr import inject


class TestQuotePath(unittest.TestCase):
    def test_a_plain_path_is_untouched(self):
        self.assertEqual(r"D:\ws\projV\src\main.py",
                         inject.quote_path(r"D:\ws\projV\src\main.py"))

    def test_a_space_is_quoted(self):
        self.assertEqual(r'"D:\ws\my project"', inject.quote_path(r"D:\ws\my project"))

    def test_the_shell_characters_are_quoted(self):
        for name in ("a&b", "a(b)", "a;b", "a|b", "a!b", "a^b", "a=b", "a,b",
                     "a[b]", "a{b}", "a<b", "a>b", "a$b", "a'b", "a`b"):
            with self.subTest(name=name):
                self.assertTrue(inject.quote_path(r"D:\ws\%s" % name).startswith('"'),
                                name)

    def test_a_trailing_backslash_keeps_its_quote_alive(self):
        # "D:\" would be parsed as an escaped quote and swallow the rest of the
        # command line, so the backslash is doubled.
        self.assertEqual('"D:\\\\"', inject.quote_path("D:\\"))

    def test_a_quote_inside_a_name_is_dropped_and_says_so(self):
        # Windows forbids `"` in a file name, so this only happens with a string
        # that was never a path; dropping it keeps the shell from being confused.
        self.assertEqual(r'"D:\a\b"', inject.quote_path('D:\\a"\\b'))

    def test_empty_stays_empty(self):
        self.assertEqual("", inject.quote_path(""))
        self.assertFalse(inject.needs_quotes(""))


class TestPayload(unittest.TestCase):
    def test_the_at_form_prefixes_the_quoted_text(self):
        self.assertEqual(r"@D:\ws\a.py", inject.payload(r"D:\ws\a.py", True))
        self.assertEqual(r'@"D:\ws\a b.py"', inject.payload(r"D:\ws\a b.py", True))

    def test_the_plain_form_has_no_prefix(self):
        self.assertEqual(r"D:\ws\a b.py".join(('"', '"')),
                         inject.payload(r"D:\ws\a b.py"))

    def test_no_path_sends_nothing(self):
        # The pane must not receive a lone "@" or a pair of empty quotes.
        self.assertEqual("", inject.payload("", True))
        self.assertEqual("", inject.payload("", False))


if __name__ == "__main__":
    unittest.main()
