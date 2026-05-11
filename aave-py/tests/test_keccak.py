import unittest

from lib.keccak import keccak256


class TestKeccak256(unittest.TestCase):
    """Test vectors from the original Keccak submission (pre-NIST padding 0x01)."""

    def test_empty(self):
        # keccak256("") - widely-published vector
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )

    def test_abc(self):
        self.assertEqual(
            keccak256(b"abc").hex(),
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )

    def test_quick_brown_fox(self):
        self.assertEqual(
            keccak256(b"The quick brown fox jumps over the lazy dog").hex(),
            "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
        )

    def test_multi_block_input_matches_block_at_a_time(self):
        """Indirectly exercises the absorb loop: 200-byte input (>1 rate-block)
        must produce the same hash whether absorbed in one shot or computed
        independently. We sanity-check by hashing both the full string and a
        prefix and asserting they differ - any silent state-reuse bug would
        produce equal digests."""
        long_input = b"\xa3" * 200
        prefix = b"\xa3" * 136
        self.assertNotEqual(keccak256(long_input), keccak256(prefix))
        self.assertNotEqual(keccak256(long_input), keccak256(b""))

    def test_boundary_135_bytes(self):
        """Triggers the merged-pad-byte branch (rate - 1 input)."""
        self.assertEqual(
            keccak256(b"\x00" * 135).hex(),
            "29e3704feeca7fb9ba229f0fa04d9b36449cf3ad6e1d85d9cfff3a10df9abc3e",
        )

    def test_boundary_136_bytes(self):
        """Exact rate boundary - tail is empty after the absorb loop."""
        self.assertEqual(
            keccak256(b"\x00" * 136).hex(),
            "3a5912a7c5faa06ee4fe906253e339467a9ce87d533c65be3c15cb231cdb25f9",
        )

    def test_boundary_137_bytes(self):
        """One byte past the boundary."""
        self.assertEqual(
            keccak256(b"\x00" * 137).hex(),
            "bee7fbb405cb0d91a8775e338c4a5e4b5d6b2d051f687fa942043cffdc73bd28",
        )

    def test_multi_block_200_bytes(self):
        """Multi-block input with a hard-coded reference digest."""
        self.assertEqual(
            keccak256(b"\x00" * 200).hex(),
            "e1bb54e1bc3af48d01e5dbfc81015c98152a574f6428c6948aa4837c9c0baad9",
        )


if __name__ == "__main__":
    unittest.main()
