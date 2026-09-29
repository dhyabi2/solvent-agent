"""
nano_rail.py — SOLVENT's optional feeless spend rail (Nano / XNO).

Alongside Stripe Issuing, SOLVENT can settle a vendor bill on Nano (XNO), a
rail where a payment settles feeless with single-block finality, so a small
provisioning bill arrives whole with no processing floor eating it.

Opt-in only. The rail is used for vendor spend only when SOLVENT_NANO_RAIL is
set to 1/true/yes; otherwise `spend_rail()` returns the Stripe client and this
module is never exercised.

This is a *spend-only*, simulate-only client for now. It models paying a
Nano-capable vendor endpoint via the exact x402 handshake. With no
NANO_WALLET_ADDRESS it runs a deterministic simulator, like StripeClient does
without a key. With a wallet configured it fails closed: `pay_vendor` raises
NotImplementedError, because signing, publishing and confirming a real block
is not implemented and a derived id must never be reported as a real payment.

Configuration (all optional, nothing has a network default):
  SOLVENT_NANO_RAIL      1/true/yes to route vendor spend through this rail
  NANO_FACILITATOR_URL   base URL of an x402 facilitator; `/supported` is read
  NANO_WALLET_ADDRESS    presence switches the rail to live mode (which raises)
  NANO_XNO_PRICE_CENTS   fixed XNO price in USD cents (default 1000 = $10.00)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import uuid
from typing import Any

NANO_SCHEME = "exact"
NANO_NETWORK = "nano:mainnet"
EXACT_DEFAULT_WORK_THRESHOLD = "fffffff800000000"

# 1 XNO == 10**30 raw. All amounts are computed in integer raw.
RAW_PER_XNO = 10**30
# The simulator's fixed XNO price in USD cents. This is not a price oracle:
# override it with NANO_XNO_PRICE_CENTS; a live rail would need a real feed.
DEMO_XNO_PRICE_CENTS = 1000

FACILITATOR_TIMEOUT_S = 5.0

# The x402 payment status codes SOLVENT understands, matching the project's
# vendored x402 vocabulary: a 402 lists the rail; 400 / 410 say the quote or
# the spendable payload is stale and must be re-quoted.
STATUS_PAYMENT_REQUIRED = 402
STATUS_PAYMENT_ACCEPTED = 200

_TRUTHY = ("1", "true", "yes")


def nano_rail_enabled() -> bool:
    """True only when SOLVENT_NANO_RAIL is explicitly set to 1/true/yes."""
    return os.environ.get("SOLVENT_NANO_RAIL", "").strip().lower() in _TRUTHY


def spend_rail(stripe: Any) -> Any:
    """The client vendor spend goes through: NanoRail if opted in, else Stripe."""
    return NanoRail() if nano_rail_enabled() else stripe


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nanoraw(cents: int, price_cents: int) -> int:
    """Convert USD cents to Nano raw at `price_cents` USD cents per XNO.

    Pure integer math (1 XNO == 10**30 raw). A remainder rounds up, so the
    vendor is never paid less than the bill.
    """
    if not _is_int(cents) or cents < 0:
        raise ValueError("amount must be a non-negative int")
    if not _is_int(price_cents) or price_cents <= 0:
        raise ValueError("price_cents must be a positive int")
    return -(-cents * RAW_PER_XNO // price_cents)


def _price_from_env() -> int:
    value = os.environ.get("NANO_XNO_PRICE_CENTS", "").strip()
    if not value:
        return DEMO_XNO_PRICE_CENTS
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("NANO_XNO_PRICE_CENTS must be a positive integer (USD cents)")
    return int(value)


class NanoRail:
    """Pay a Nano-capable vendor endpoint feelessly via the exact x402 scheme.

    Uses the same contract shape as StripeClient.pay_vendor: returns a dict
    that mirrors the treasury spend fields (`vendor`, `amount_cents`, `memo`,
    `simulated`, `ts`). Only simulate mode returns; live mode raises.
    """

    def __init__(self) -> None:
        facilitator = os.environ.get("NANO_FACILITATOR_URL", "").strip().rstrip("/")
        self.facilitator: str | None = facilitator or None
        self.wallet_address = os.environ.get("NANO_WALLET_ADDRESS", "").strip()
        self.live = bool(self.wallet_address)
        self.price_cents = _price_from_env()

    def _fetch_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=FACILITATOR_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def supported(self) -> bool:
        """Whether the configured facilitator's /supported lists exact on Nano mainnet.

        Reads the x402 `/supported` response (`{"kinds": [{"scheme", "network"},
        ...]}`). No configured facilitator, an unreachable one, or a malformed
        response all mean False.
        """
        if not self.facilitator:
            return False
        try:
            data = self._fetch_json(f"{self.facilitator}/supported")
        except Exception:
            return False
        kinds = data.get("kinds") if isinstance(data, dict) else None
        if not isinstance(kinds, list):
            return False
        return any(
            isinstance(kind, dict)
            and kind.get("scheme") == NANO_SCHEME
            and kind.get("network") == NANO_NETWORK
            for kind in kinds
        )

    def quote_endpoint(self, vendor: str, amount_cents: int) -> dict[str, Any]:
        """Step 1 of the exact x402 handshake: the seller's payment-required quote."""
        if not _is_int(amount_cents) or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive int")
        return {
            "status": STATUS_PAYMENT_REQUIRED,
            "mimeType": vendor,
            "payment": {
                "scheme": NANO_SCHEME,
                "network": NANO_NETWORK,
                "asset": "XNO",
                "amount": str(_nanoraw(amount_cents, self.price_cents)),
                "work": "required",
                "workThreshold": EXACT_DEFAULT_WORK_THRESHOLD,
            },
        }

    def pay_vendor(
        self, vendor: str, amount_cents: int, memo: str, job_id: str | None = None
    ) -> dict[str, Any]:
        """Simulated feeless payment to a Nano-capable vendor.

        Mirrors StripeClient.pay_vendor's return contract so `stages.py` books
        the expense unchanged. Always `simulated: True`. In live mode (a wallet
        is configured) it raises NotImplementedError: no block is signed, sent
        or confirmed here, so nothing may be reported as a real payment.
        """
        if not _is_int(amount_cents) or amount_cents < 0:
            raise ValueError("amount_cents must be a non-negative int")
        if self.live:
            raise NotImplementedError(
                "live Nano payments are not implemented; unset NANO_WALLET_ADDRESS "
                "to simulate, or SOLVENT_NANO_RAIL to spend through Stripe"
            )
        quote = self.quote_endpoint(vendor, amount_cents)
        hash_input = f"solvent-demo:{vendor}:{amount_cents}:{memo}".encode()
        # Deterministic content-addressed id for the simulated block (no network write)
        block_id = hashlib.sha256(hash_input).hexdigest()[:32]
        return {
            "id": "xno_sim_" + uuid.uuid4().hex[:12],
            "vendor": vendor,
            "amount_cents": amount_cents,
            "memo": memo,
            "simulated": True,
            "rail": "nano",
            "network": NANO_NETWORK,
            "quote": quote,
            "block": block_id,
            "job_id": job_id,
            "ts": time.time(),
        }
