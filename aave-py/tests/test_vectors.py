import unittest

from lib import vectors


class TestVectors(unittest.TestCase):
    def test_run_all_returns_true_on_clean_run(self):
        ok, results = vectors.run_all()
        self.assertTrue(ok, f"selftest failed: {results}")
        # Every result should be (name, passed=True, detail=None|str)
        for name, passed, detail in results:
            self.assertTrue(passed, f"{name} failed: {detail}")

    def test_results_cover_every_module(self):
        _, results = vectors.run_all()
        names = {r[0] for r in results}
        for needed in ("keccak", "rlp", "abi", "bip39", "bip32", "eip55", "eip1559"):
            self.assertIn(needed, names)


if __name__ == "__main__":
    unittest.main()
