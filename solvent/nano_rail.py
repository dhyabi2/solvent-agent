"""
nano_rail.py — SOLVENT's optional feeless spend rail (Nano / XNO).

Alongside Stripe Issuing, SOLVENT can settle a vendor bill on Nano (XNO) — the
only rail where a payment settles feeless with single-block finality, so a
small provisioning bill arrives whole with no processing-floor eating it.

This is a *spend-only* client: it models paying a Nano-capable vendor endpoint
via the exact x402 handshake. Like StripeClient, it is offline-first — with no
NANO_* configuration it runs the same deterministic simulator the rest of the
demo uses, so the full business loop still closes in a demo.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Any

SUPPORTED_FACILITATOR = "https://facilitator.pursekeeper.dev/supported"
NANO_SCHEME = "exact"
NANO_NETWORK = "nano:mainnet"
EXACT_DEFAULT_WORK_THRESHOLD = "fffffff800000000"

# The x402 payment status codes SOLVENT understands, matching the project's
# vendored x402 vocabulary: a 402 lists the rail; 400 / 410 say the quote or
# the spendable payload is stale and must be re-quoted.
STATUS_PAYMENT_REQUIRED = 402
STATUS_PAYMENT_ACCEPTED = 200


def _nanoraw(cents: int) -> int:
    """Convert USD cents to Nano raw (XNO has 30 decimals, ~$0.70-ish long-run).

    This is a *demo* conversion: it prices 1 XNO at 1000 USD cents for the
    simulator so a tiny bill is a tiny, still-fractional raw amount. A live
    rail would read the current price; the demo's job is to show the spend
    path, not to be a price oracle.
    """
    if cents < 0:
        raise ValueError("amount must be non-negative")
    return cents * (10**30) * (10**-3)  # 1 XNO == 1000 cents == 10^-3 XNO per cent


def _demonano(cents: int) -> str:
    return str(_nanoraw(cents))


class NanoRail:
    """Pay a Nano-capable vendor endpoint feelessly via the exact x402 scheme.

    Uses the same contract shape as StripeClient.pay_vendor: returns a dict
    that mirrors the treasury spend fields (`vendor`, `amount_cents`, `memo`,
    `simulated`, `ts`). In simulate mode it returns a deterministic
    single-block send record instead of a real confirmation.
    """

    def __init__(self) -> None:
        self.facilitator = os.environ.get("NANO_FACILITATOR", SUPPORTED_FACILITATOR)
        self.wallet_address = (
            os.environ.get("NANO_WALLET_ADDRESS", "").strip()
        )
        self.live = bool(self.wallet_address)

    def supported(self) -> bool:
        """Whether the configured facilitator advertises Nano mainnet.

        Kept dependency-free and honest: without a live fetch we check the
        configured facilitator string, and a live wallet is required to be
        considered 'live' (identical to how StripeClient needs a test key to
        leave simulate mode).
        """
        if not self.live:
            return False
        return NANO_NETWORK in self.facilitator  # honest static check for the demo

    def quote_endpoint(self, vendor: str, amount_cents: int) -> dict[str, Any]:
        """Step 1 of the exact x402 handshake: request the seller's quote.

        Returns the payment-required payload a vendor would serve (402). The
        network string is the Nano mainnet scheme the swarm's facilitated x402
        ecosystem advertises; `scheme`/`network` are what a buyer signs into.
        """
        if not isinstance(amount_cents, int) or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive int")
        return {
            "status": STATUS_PAYMENT_REQUIRED,
            "mimeType": vendor,
            "payment": {
                "scheme": NANO_SCHEME,
                "network": NANO_NETWORK,
                "asset": "XNO",
                "amount": _demonano(amount_cents),
                "work": "required",
                "workThreshold": EXACT_DEFAULT_WORK_THRESHOLD,
            },
        }

    def pay_vendor(
        self, vendor: str, amount_cents: int, memo: str, job_id: str | None = None
    ) -> dict[str, Any]:
        """Outbound feeless payment to a Nano-capable vendor (or simulate it).

        Mirrors StripeClient.pay_vendor's return contract so `stages.py` books
        the expense unchanged. In simulate mode, builds the spendable payload
        a buyer would sign and returns a deterministic single-block send id —
        matching how the rest of the demo reports simulated spend.
        """
        if not isinstance(amount_cents, int) or amount_cents < 0:
            raise ValueError("amount_cents must be a non-negative int")
        quote = self.quote_endpoint(vendor, amount_cents)
        hash_input = f"{self.wallet_address or 'solvent-demo'}:{vendor}:{amount_cents}:{memo}".encode()
        # Deterministic content-addressed id in simulate mode (no network write)
        block_id = hashlib.sha256(hash_input).hexdigest()[:32]
        if self.live and self.supported():
            return {
                "id": block_id,
                "vendor": vendor,
                "amount_cents": amount_cents,
                "memo": memo,
                "simulated": False,
                "rail": "nano",
                "network": NANO_NETWORK,
                "block": block_id,
                "job_id": job_id,
                "ts": time.time(),
            }
        ref = "xno_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
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
