import json
import unittest
import unittest.mock
from io import BytesIO
from unittest.mock import patch

from lib.jsonrpc import RpcClient, JsonRpcError


def _fake_response(payload: dict) -> BytesIO:
    body = json.dumps(payload).encode("utf-8")
    resp = BytesIO(body)
    return resp


class TestRpcClient(unittest.TestCase):
    def test_call_returns_result(self):
        client = RpcClient("https://example/rpc")
        with patch("lib.jsonrpc.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = _fake_response(
                {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
            )
            out = client.call("eth_blockNumber", [])
        self.assertEqual(out, "0x10")

    def test_call_raises_on_error_object(self):
        client = RpcClient("https://example/rpc")
        with patch("lib.jsonrpc.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = _fake_response(
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "nonce too low"}}
            )
            with self.assertRaises(JsonRpcError) as cm:
                client.call("eth_sendRawTransaction", ["0x02..."])
        self.assertIn("nonce too low", str(cm.exception))
        self.assertEqual(cm.exception.code, -32000)

    def test_request_id_increments(self):
        client = RpcClient("https://example/rpc")
        ids: list[int] = []
        with patch("lib.jsonrpc.urlopen") as urlopen:
            def capture(req, timeout):
                body = json.loads(req.data.decode("utf-8"))
                ids.append(body["id"])
                ctx = unittest.mock.MagicMock()
                ctx.__enter__.return_value = _fake_response({"jsonrpc": "2.0", "id": body["id"], "result": "0x0"})
                ctx.__exit__.return_value = False
                return ctx
            urlopen.side_effect = capture
            client.call("eth_blockNumber", [])
            client.call("eth_blockNumber", [])
        self.assertEqual(ids, [1, 2])


if __name__ == "__main__":
    unittest.main()
