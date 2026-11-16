"""OpenAI-compatible HTTP transport shared by every benchmark system."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from tokenprov.broker.base import BrokerUnavailable
from tokenprov.server_eval.config import safe_url


class JsonHttpClient:
    def __init__(self, base_url, api_key=None, timeout_s=180):
        self.base_url = safe_url(base_url, "base_url")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def request(self, path, payload=None):
        base = self.base_url
        # Accept both http://host:port and http://host:port/v1.
        if base.endswith("/v1") and path.startswith("/v1/"):
            path = path[3:]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(base + path,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
            headers=headers, method="GET" if payload is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                # Refuse unexpectedly large errors/responses instead of unbounded allocations.
                body = response.read(32 * 1024 * 1024 + 1)
                if len(body) > 32 * 1024 * 1024:
                    raise BrokerUnavailable("HTTP response exceeds 32 MiB")
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise BrokerUnavailable("HTTP response must be a JSON object")
                return value
        except urllib.error.HTTPError as exc:
            # Never persist arbitrary server bodies, headers, or credential-bearing URLs.
            raise BrokerUnavailable(f"model service returned HTTP {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise BrokerUnavailable(f"model service transport failed ({type(exc).__name__})") from None

    def completion(self, call):
        payload = {"model": call.configuration.model_id,
                   "messages": list(call.transformed_messages),
                   **call.configuration.decoding_config}
        raw = self.request("/v1/chat/completions", payload)
        parse_completion(raw)
        return raw


def parse_completion(raw):
    try:
        text = raw["choices"][0]["message"]["content"]
        usage = raw["usage"]
        counts = (usage["prompt_tokens"], usage["completion_tokens"])
        if not isinstance(text, str) or any(type(x) is not int or x < 0 for x in counts):
            raise ValueError("invalid text or usage")
        return text, counts[0], counts[1]
    except (KeyError, IndexError, TypeError, ValueError):
        raise BrokerUnavailable("completion must include text and nonnegative integer provider usage") from None
