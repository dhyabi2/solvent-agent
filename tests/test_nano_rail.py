"""Tests for SOLVENT Nano (XNO) feeless spend rail."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent import nano_rail
from solvent.guardrails import Guardrails
from solvent.nano_rail import (
    DEMO_XNO_PRICE_CENTS,
    EXACT_DEFAULT_WORK_THRESHOLD,
    NANO_NETWORK,
    NANO_SCHEME,
    RAW_PER_XNO,
    NanoRail,
    _nanoraw,
    nano_rail_enabled,
    spend_rail,
)
from solvent.pricing import PricingPolicy
from solvent.stages import StageRunner
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury

CLEAN_ENV = {
    "NANO_WALLET_ADDRESS": "",
    "NANO_FACILITATOR_URL": "",
    "NANO_XNO_PRICE_CENTS": "",
    "SOLVENT_NANO_RAIL": "",
}

SUPPORTED_NANO = {
    "kinds": [
        {"x402Version": 1, "scheme": "exact", "network": "base-sepolia"},
        {"x402Version": 1, "scheme": "exact", "network": "nano:mainnet"},
    ]
}


class _CleanEnv(unittest.TestCase):
    extra_env: dict = {}

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {**CLEAN_ENV, **self.extra_env}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()


class TestNanoRailSimulate(_CleanEnv):
    def test_offline_by_default(self):
        client = NanoRail()
        self.assertFalse(client.live)
        self.assertFalse(client.supported())

    def test_quote_endpoint_advertises_nano_scheme(self):
        client = NanoRail()
        quote = client.quote_endpoint("market-data.example", 250)
        self.assertEqual(quote["status"], 402)
        self.assertEqual(quote["payment"]["scheme"], NANO_SCHEME)
        self.assertEqual(quote["payment"]["network"], NANO_NETWORK)
        self.assertEqual(quote["payment"]["asset"], "XNO")
        self.assertEqual(quote["payment"]["amount"], str(250 * RAW_PER_XNO // 1000))
        self.assertEqual(quote["payment"]["workThreshold"], EXACT_DEFAULT_WORK_THRESHOLD)

    def test_quote_rejects_nonpositive_amount(self):
        client = NanoRail()
        with self.assertRaises(ValueError):
            client.quote_endpoint("v.example", 0)

    def test_pay_vendor_simulate_returns_spendable_record(self):
        client = NanoRail()
        payment = client.pay_vendor("market-data.example", 250, "provision", job_id="j1")
        self.assertTrue(payment["simulated"])
        self.assertEqual(payment["rail"], "nano")
        self.assertEqual(payment["network"], NANO_NETWORK)
        self.assertEqual(payment["amount_cents"], 250)
        self.assertTrue(payment["id"].startswith("xno_sim_"))
        self.assertEqual(len(payment["block"]), 32)

    def test_pay_vendor_reflects_quote(self):
        client = NanoRail()
        payment = client.pay_vendor("v.example", 100, "memo")
        self.assertEqual(payment["quote"]["payment"]["scheme"], NANO_SCHEME)
        self.assertEqual(payment["quote"]["payment"]["asset"], "XNO")

    def test_rejects_negative_amount(self):
        client = NanoRail()
        with self.assertRaises(ValueError):
            client.pay_vendor("v.example", -1, "memo")


class TestNoDefaultFacilitator(_CleanEnv):
    def test_no_facilitator_unless_configured(self):
        client = NanoRail()
        self.assertIsNone(client.facilitator)

    def test_module_carries_no_facilitator_host(self):
        source = Path(nano_rail.__file__).read_text(encoding="utf-8")
        self.assertNotIn("https://", source)
        self.assertNotIn("pursekeeper", source)

    def test_unconfigured_facilitator_is_never_fetched(self):
        with mock.patch.dict(os.environ, {"NANO_WALLET_ADDRESS": "nano_1abc"}):
            client = NanoRail()
            with mock.patch.object(client, "_fetch_json") as fetch:
                self.assertFalse(client.supported())
                fetch.assert_not_called()

    def test_configured_facilitator_is_used_as_given(self):
        with mock.patch.dict(os.environ, {"NANO_FACILITATOR_URL": "https://f.example/"}):
            client = NanoRail()
            self.assertEqual(client.facilitator, "https://f.example")
            with mock.patch.object(client, "_fetch_json", return_value=SUPPORTED_NANO) as fetch:
                client.supported()
                fetch.assert_called_once_with("https://f.example/supported")


class TestSupportedParsesResponse(_CleanEnv):
    extra_env = {"NANO_FACILITATOR_URL": "https://f.example"}

    def _supported(self, response=None, error=None):
        client = NanoRail()
        with mock.patch.object(client, "_fetch_json", return_value=response, side_effect=error):
            return client.supported()

    def test_true_when_response_lists_nano_exact(self):
        self.assertTrue(self._supported(SUPPORTED_NANO))

    def test_false_when_response_lacks_nano(self):
        self.assertFalse(self._supported({"kinds": [{"scheme": "exact", "network": "base"}]}))

    def test_false_when_nano_listed_under_another_scheme(self):
        self.assertFalse(self._supported({"kinds": [{"scheme": "upto", "network": NANO_NETWORK}]}))

    def test_url_string_alone_proves_nothing(self):
        with mock.patch.dict(os.environ, {"NANO_FACILITATOR_URL": "https://nano:mainnet.example"}):
            self.assertFalse(self._supported({"kinds": []}))

    def test_malformed_responses_are_false(self):
        for bad in (None, [], "nano:mainnet", {"kinds": "nano:mainnet"}, {"kinds": [1, None]}):
            with self.subTest(bad=bad):
                self.assertFalse(self._supported(bad))

    def test_fetch_error_is_false(self):
        self.assertFalse(self._supported(error=OSError("unreachable")))


class TestFailClosedLive(_CleanEnv):
    extra_env = {"NANO_WALLET_ADDRESS": "nano_1abc", "NANO_FACILITATOR_URL": "https://f.example"}

    def test_live_pay_vendor_raises(self):
        client = NanoRail()
        self.assertTrue(client.live)
        with self.assertRaises(NotImplementedError):
            client.pay_vendor("v.example", 100, "memo")

    def test_live_raises_even_when_facilitator_supports_nano(self):
        client = NanoRail()
        with mock.patch.object(client, "_fetch_json", return_value=SUPPORTED_NANO):
            self.assertTrue(client.supported())
            with self.assertRaises(NotImplementedError):
                client.pay_vendor("v.example", 100, "memo")


class TestIntegerRawMath(_CleanEnv):
    def test_returns_exact_int(self):
        raw = _nanoraw(250, DEMO_XNO_PRICE_CENTS)
        self.assertIsInstance(raw, int)
        self.assertEqual(raw, 250 * RAW_PER_XNO // DEMO_XNO_PRICE_CENTS)

    def test_one_xno_is_ten_to_the_thirty_raw(self):
        self.assertEqual(RAW_PER_XNO, 10**30)
        self.assertEqual(_nanoraw(DEMO_XNO_PRICE_CENTS, DEMO_XNO_PRICE_CENTS), 10**30)

    def test_large_amount_is_exact(self):
        cents = 123_456_789_123
        self.assertEqual(_nanoraw(cents, 1000), cents * 10**27)

    def test_uneven_division_rounds_up(self):
        # the vendor is never paid less than the bill
        self.assertEqual(_nanoraw(1, 3), 10**30 // 3 + 1)

    def test_rejects_bad_inputs(self):
        for cents, price in ((-1, 1000), (1, 0), (1, -5), (1.5, 1000), (1, 2.5), (True, 1000)):
            with self.subTest(cents=cents, price=price):
                with self.assertRaises(ValueError):
                    _nanoraw(cents, price)

    def test_price_comes_from_env_when_set(self):
        with mock.patch.dict(os.environ, {"NANO_XNO_PRICE_CENTS": "85"}):
            client = NanoRail()
            self.assertEqual(client.price_cents, 85)
            quote = client.quote_endpoint("v.example", 85)
            self.assertEqual(quote["payment"]["amount"], str(10**30))

    def test_invalid_price_env_is_rejected(self):
        for bad in ("0", "-3", "0.7", "abc"):
            with self.subTest(bad=bad):
                with mock.patch.dict(os.environ, {"NANO_XNO_PRICE_CENTS": bad}):
                    with self.assertRaises(ValueError):
                        NanoRail()


class TestOptInGate(_CleanEnv):
    def _runner(self, tmp: str) -> StageRunner:
        t = Treasury(path=Path(tmp) / "t.db")
        t.seed(50_000)
        return StageRunner(
            treasury=t, guard=Guardrails(t), stripe=StripeClient(), pricing=PricingPolicy()
        )

    def test_off_by_default(self):
        self.assertFalse(nano_rail_enabled())
        stripe = StripeClient()
        self.assertIs(spend_rail(stripe), stripe)

    def test_off_for_non_truthy_values(self):
        for value in ("0", "false", "no", "off", "nano"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"SOLVENT_NANO_RAIL": value}):
                    self.assertFalse(nano_rail_enabled())

    def test_on_only_when_set(self):
        for value in ("1", "true", "yes", " TRUE "):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"SOLVENT_NANO_RAIL": value}):
                    self.assertTrue(nano_rail_enabled())
                    self.assertIsInstance(spend_rail(StripeClient()), NanoRail)

    def test_stage_runner_spends_on_stripe_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(tmp)
            self.assertIs(runner.spend_rail, runner.stripe)

    def test_stage_runner_spends_on_nano_when_enabled(self):
        with mock.patch.dict(os.environ, {"SOLVENT_NANO_RAIL": "1"}):
            with tempfile.TemporaryDirectory() as tmp:
                runner = self._runner(tmp)
                self.assertIsInstance(runner.spend_rail, NanoRail)
                pay = runner.spend_rail.pay_vendor("v.example", 100, "memo")
                self.assertTrue(pay["simulated"])

    def test_enabled_live_rail_raises_instead_of_paying(self):
        env = {"SOLVENT_NANO_RAIL": "1", "NANO_WALLET_ADDRESS": "nano_1abc"}
        with mock.patch.dict(os.environ, env):
            with tempfile.TemporaryDirectory() as tmp:
                runner = self._runner(tmp)
                with self.assertRaises(NotImplementedError):
                    runner.spend_rail.pay_vendor("v.example", 100, "memo")


if __name__ == "__main__":
    unittest.main()
