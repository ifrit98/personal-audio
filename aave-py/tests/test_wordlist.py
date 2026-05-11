import unittest
from pathlib import Path


class TestWordlist(unittest.TestCase):
    def test_wordlist_is_canonical(self):
        path = Path(__file__).parent.parent / "wordlist.txt"
        words = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(words), 2048, "BIP-39 wordlist must have exactly 2048 words")
        # Canonical first and last word per the BIP-39 English wordlist.
        self.assertEqual(words[0], "abandon")
        self.assertEqual(words[-1], "zoo")
        # Specific known indices for sanity:
        self.assertEqual(words[3], "about")  # 4th word
        self.assertEqual(words[1023], "lend")
        self.assertEqual(words[2047], "zoo")
        # All lowercase ASCII, no surrounding whitespace.
        for i, w in enumerate(words):
            self.assertTrue(w.isascii() and w.islower(), f"word {i} not ascii lowercase: {w!r}")
            self.assertEqual(w, w.strip(), f"word {i} has whitespace: {w!r}")


if __name__ == "__main__":
    unittest.main()
