"""Minimal JSON-RPC 2.0 client over urllib.

Implements only what we need: a single-method `call`, retry on transient
network errors, raise on JSON-RPC error objects.
"""

from __future__ import annotations

import json
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT_SECS = 30
RETRIES = 3


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: object = None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class RpcClient:
    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT_SECS):
        self.url = url
        self.timeout = timeout
        self._next_id = 1

    def call(self, method: str, params: list) -> object:
        request_id = self._next_id
        self._next_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode("utf-8")
        req = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_err: Exception | None = None
        for attempt in range(RETRIES):
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                payload = json.loads(data.decode("utf-8"))
                if "error" in payload:
                    err = payload["error"]
                    raise JsonRpcError(err.get("code", 0), err.get("message", "?"), err.get("data"))
                return payload["result"]
            except (URLError, HTTPError, TimeoutError) as e:
                last_err = e
                if attempt < RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        assert last_err is not None
        raise last_err
