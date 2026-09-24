"""Tests for SOLVENT Nano (XNO) feeless spend rail."""

import os
import unittest
from unittest import mock

from solvent.nano_rail import (
    EXACT_DEFAULT_WORK_THRESHOLD,
    NANO_NETWORK,
    NANO_SCHEME,
    NanoRail,
)


class TestNanoRailSimulate(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(
            os.environ,
            {"NANO_WALLET_ADDRESS": ""},
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

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
        # a deterministic spendable block is present even in simulate mode
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


if __name__ == "__main__":
    unittest.main()
