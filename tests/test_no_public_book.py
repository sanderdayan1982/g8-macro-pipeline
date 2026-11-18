"""El libro no está en el repo público.

orden Sander 18-nov t244u
"""
import fnmatch
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class NoPublicBook(unittest.TestCase):
    def test_no_book_csv_in_the_public_repo(self):
        # orden Sander 18-nov t244u
        hits = []
        data = os.path.join(ROOT, "data")
        if os.path.isdir(data):
            for name in sorted(os.listdir(data)):
                # data/book.csv y cualquier data/book*.csv (no recursivo)
                if fnmatch.fnmatch(name, "book*.csv"):
                    hits.append("data/" + name)
        positions = os.path.join(ROOT, "book", "positions.csv")
        if os.path.exists(positions):
            hits.append("book/positions.csv")
        self.assertEqual(hits, [], "libro en el repo público: %s" % ", ".join(hits))


if __name__ == "__main__":
    unittest.main()
