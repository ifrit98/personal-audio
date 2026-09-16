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

    def test_http_4xx_not_retried(self):
        from urllib.error import HTTPError
        from io import BytesIO
        client = RpcClient("https://example/rpc")
        attempts = []
        def burn(req, timeout):
            attempts.append(1)
            raise HTTPError(req.full_url, 401, "Unauthorized", {}, BytesIO(b""))
        with patch("lib.jsonrpc.urlopen", side_effect=burn), patch("lib.jsonrpc.time.sleep"):
            with self.assertRaises(HTTPError):
                client.call("eth_blockNumber", [])
        self.assertEqual(len(attempts), 1, "401 should fail fast, not retry")

    def test_http_5xx_retries(self):
        from urllib.error import HTTPError
        from io import BytesIO
        import json as _json
        client = RpcClient("https://example/rpc")
        attempts = []
        def flaky(req, timeout):
            attempts.append(1)
            if len(attempts) < 3:
                raise HTTPError(req.full_url, 502, "Bad Gateway", {}, BytesIO(b""))
            body = _json.dumps({"jsonrpc": "2.0", "id": _json.loads(req.data)["id"], "result": "0xab"}).encode()
            class Ctx:
                def __enter__(self): return _fake_response(_json.loads(body))
                def __exit__(self, *a): return False
            return Ctx()
        with patch("lib.jsonrpc.urlopen", side_effect=flaky), patch("lib.jsonrpc.time.sleep"):
            out = client.call("eth_blockNumber", [])
        self.assertEqual(out, "0xab")
        self.assertEqual(len(attempts), 3)


if __name__ == "__main__":
    unittest.main()
