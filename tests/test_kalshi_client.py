"""Kalshi client tests: RSA-PSS signing, request shaping, retries, book parse."""
import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.kalshi_client import KalshiAPIError, KalshiClient
from src.marketdata import parse_yes_top
from src.models import OrderRequest


def make_key(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p = tmp_path / "k.pem"
    p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return p, key.public_key()


class FakeResp:
    def __init__(self, status, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.content = b"x" if (json_data is not None or text) else b""

    def json(self):
        return self._json or {}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_signature_verifies_against_public_key(tmp_path):
    p, pub = make_key(tmp_path)
    c = KalshiClient("https://host/trade-api/v2", "keyid", str(p))
    headers = c._headers("GET", "/trade-api/v2/markets")
    ts = headers["KALSHI-ACCESS-TIMESTAMP"]
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = f"{ts}GET/trade-api/v2/markets".encode("utf-8")
    # Raises InvalidSignature if wrong -- so reaching the assert means it verified.
    pub.verify(
        sig,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    assert headers["KALSHI-ACCESS-KEY"] == "keyid"


def test_create_order_builds_limit_body(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([FakeResp(200, {"order": {"order_id": "o1", "yes_price": 98}})])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess)
    out = c.create_order(OrderRequest("MKT", "buy", "yes", 98, 10, "cid-1"))
    body = sess.calls[0]["json"]
    assert body["type"] == "limit"           # never a market order
    assert body["action"] == "buy" and body["side"] == "yes"
    assert body["yes_price"] == 98 and body["count"] == 10
    assert body["client_order_id"] == "cid-1"
    assert out["order_id"] == "o1"


def test_balance_converts_cents_to_usd(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([FakeResp(200, {"balance": 1234})])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess)
    assert c.get_balance_usd() == 12.34


def test_4xx_raises_api_error_no_retry(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([FakeResp(400, text="bad request")])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess)
    with pytest.raises(KalshiAPIError):
        c.get_balance_usd()
    assert len(sess.calls) == 1               # 4xx is not retried


def test_transient_5xx_is_retried_then_succeeds(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([FakeResp(503, text="busy"), FakeResp(200, {"balance": 500})])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess)
    assert c.get_balance_usd() == 5.0
    assert len(sess.calls) == 2               # retried once


def test_list_markets_params_passed(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([FakeResp(200, {"markets": [{"ticker": "X"}], "cursor": None})])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess)
    markets, cursor = c.list_markets_page(status="open", min_close_ts=1, max_close_ts=2, limit=5)
    assert markets[0]["ticker"] == "X" and cursor is None
    assert sess.calls[0]["params"]["status"] == "open"
    assert sess.calls[0]["params"]["min_close_ts"] == 1


def test_optional_order_controls_sent_when_configured(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([
        FakeResp(200, {"order": {"order_id": "o1"}}),
        FakeResp(200, {"order": {"order_id": "o2"}}),
    ])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess,
                     time_in_force="fill_or_kill", reduce_only_sells=True)
    c.create_order(OrderRequest("M", "buy", "yes", 98, 10, "cid-b"))
    buy = sess.calls[0]["json"]
    assert buy["time_in_force"] == "fill_or_kill"
    assert "reduce_only" not in buy            # reduce_only applies to sells only
    c.create_order(OrderRequest("M", "sell", "yes", 95, 10, "cid-s"))
    sell = sess.calls[1]["json"]
    assert sell["time_in_force"] == "fill_or_kill"
    assert sell["reduce_only"] is True


def test_optional_order_controls_omitted_by_default(tmp_path):
    p, _ = make_key(tmp_path)
    sess = FakeSession([FakeResp(200, {"order": {"order_id": "o1"}})])
    c = KalshiClient("https://host/trade-api/v2", "k", str(p), session=sess)
    c.create_order(OrderRequest("M", "buy", "yes", 98, 10, "cid"))
    body = sess.calls[0]["json"]
    assert "time_in_force" not in body and "reduce_only" not in body


# --- orderbook parsing ----------------------------------------------------
def test_parse_yes_top_translates_no_side_to_yes_ask():
    ob = {"yes": [[97, 300], [96, 100]], "no": [[3, 250], [2, 50]]}
    bid, ask, bid_depth, ask_depth = parse_yes_top(ob)
    assert bid == 97 and bid_depth == 300
    assert ask == 97 and ask_depth == 250     # 100 - best_no(3) = 97


def test_parse_yes_top_handles_empty_book():
    assert parse_yes_top({}) == (None, None, 0, 0)
