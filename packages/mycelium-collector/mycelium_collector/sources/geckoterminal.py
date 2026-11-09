"""GeckoTerminal price/symbol enrichment. Public API, no credentials."""
import json
import urllib.error
import urllib.request

from .. import config


def http_json(url: str, timeout: int = 15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": str(e)}


def token_url(mint: str) -> str:
    return config.GECKOTERMINAL_URL.format(mint=mint)
