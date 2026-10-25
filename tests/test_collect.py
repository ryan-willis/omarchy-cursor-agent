import json
import os
import tempfile
import unittest
from pathlib import Path

import collect

SUMMARY = {
    "billingCycleStart": "2026-10-15T00:18:22.000Z",
    "billingCycleEnd": "2026-11-15T00:18:22.000Z",
    "membershipType": "pro_plus",
    "isUnlimited": False,
    "individualUsage": {
        "plan": {
            "enabled": True,
            "used": 7000,
            "limit": 7000,
            "autoPercentUsed": 17.349166666666665,
            "apiPercentUsed": 70.47272727272727,
            "totalPercentUsed": 18.539307535641548,
        },
        "onDemand": {"enabled": False, "used": 0, "limit": None},
    },
    "teamUsage": {},
}


def jwt(sub):
    def part(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return __import__("base64").urlsafe_b64encode(raw).decode().rstrip("=")

    return part({"alg": "none"}) + "." + part({"sub": sub}) + ".sig"


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["XDG_STATE_HOME"] = str(self.root / "state")
        os.environ["XDG_CACHE_HOME"] = str(self.root / "cache")
        os.environ["XDG_CONFIG_HOME"] = str(self.root / "config")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pro_plus_pools_stay_separate(self):
        limits = collect.pools(SUMMARY)
        self.assertEqual(
            [item["title"] for item in limits],
            ["Grok/Cursor", "Other models"],
        )
        self.assertAlmostEqual(limits[0]["percent"], 0.17349166666666665)
        self.assertAlmostEqual(limits[1]["percent"], 0.7047272727272727)
        self.assertEqual(limits[0]["resetsAt"], "2026-11-15T00:18:22+00:00")
        self.assertNotIn("used", limits[0])

    def test_plan_label(self):
        self.assertEqual(collect.plan_label("pro_plus"), "Pro+")
        self.assertEqual(collect.plan_label("<img src=x>"), "")
        self.assertEqual(collect.plan_label("PRO_PLUS"), "Pro+")

    def test_missing_pool_is_omitted(self):
        summary = json.loads(json.dumps(SUMMARY))
        del summary["individualUsage"]["plan"]["apiPercentUsed"]
        limits = collect.pools(summary)
        self.assertEqual([item["title"] for item in limits], ["Grok/Cursor"])

    def test_markup_reset_is_dropped(self):
        summary = json.loads(json.dumps(SUMMARY))
        summary["billingCycleEnd"] = "<img src=https://example.invalid/tracker>"
        self.assertEqual(collect.pools(summary)[0]["resetsAt"], "")

    def test_unlimited_has_no_meters(self):
        summary = json.loads(json.dumps(SUMMARY))
        summary["isUnlimited"] = True
        self.assertEqual(collect.pools(summary), [])

    def test_rejects_bool_and_negative_percents(self):
        summary = json.loads(json.dumps(SUMMARY))
        summary["individualUsage"]["plan"]["autoPercentUsed"] = True
        with self.assertRaises(collect.UsageError):
            collect.pools(summary)
        summary["individualUsage"]["plan"]["autoPercentUsed"] = -1
        with self.assertRaises(collect.UsageError):
            collect.pools(summary)

    def test_team_pool_when_individual_is_empty(self):
        summary = {
            "membershipType": "team",
            "billingCycleEnd": "2026-11-15T00:18:22.000Z",
            "individualUsage": {},
            "teamUsage": {"plan": {"enabled": True, "autoPercentUsed": 10, "apiPercentUsed": 20}},
        }
        limits = collect.pools(summary)
        self.assertEqual(limits[1]["percent"], 0.2)

    def test_cookie_uses_user_id_not_the_whole_subject(self):
        token = jwt("auth0|user_123")
        self.assertEqual(collect.jwt_user(token), "user_123")
        cookie = collect.session_cookie(token)
        self.assertTrue(cookie.startswith("user_123%3A%3A"))
        self.assertNotIn("auth0", cookie.split("%3A%3A", 1)[0])

    def test_collect_writes_plan_without_the_session(self):
        token = jwt("auth0|user_123")
        record = collect.collect(token=token, fetch=lambda _token: SUMMARY, clock=lambda: 1000)
        saved = json.loads(collect.usage_path().read_text())
        self.assertEqual(saved["tierLabel"], "Pro+")
        self.assertEqual(saved["scope"], "account")
        self.assertFalse(saved["limitsStale"])
        self.assertEqual(len(saved["limits"]), 2)
        self.assertNotIn(token, json.dumps(saved))
        self.assertEqual(record["name"], "Cursor")
        self.assertEqual(oct(collect.usage_path().stat().st_mode & 0o777), "0o600")

    def test_failure_keeps_the_previous_meters(self):
        token = jwt("auth0|user_123")
        collect.collect(token=token, fetch=lambda _token: SUMMARY, clock=lambda: 1000)

        def fail(_token):
            raise collect.UsageError(collect.UNREACHABLE)

        record = collect.collect(token=token, fetch=fail, clock=lambda: 2000)
        self.assertTrue(record["limitsStale"])
        self.assertEqual(record["usageStatusText"], "Cursor limits unavailable")
        self.assertEqual(record["limits"][0]["title"], "Grok/Cursor")
        self.assertEqual(record["tierLabel"], "Pro+")

    def test_signed_out_record_has_no_meters(self):
        record = collect.collect(token="", fetch=lambda _token: SUMMARY)
        self.assertEqual(record["tierLabel"], "Signed out")
        self.assertEqual(record["limits"], [])
        self.assertEqual(record["authHelpText"], collect.SIGN_IN)
        self.assertNotIn("<", record["authHelpText"])


    def test_grok_bot_meter_is_added(self):
        token = jwt("auth0|user_123")
        status = {
            "usagePercent": 6.0857,
            "hasNonZeroIncludedLimit": True,
            "nextResetTimestampUtc": "2026-10-30T16:24:17.935Z",
        }
        record = collect.collect(
            token=token, fetch=lambda _t: SUMMARY, fetch_grok_bot=lambda _t: status, clock=lambda: 1000
        )
        self.assertEqual(
            [item["title"] for item in record["limits"]],
            ["Grok/Cursor", "Other models", "Grok Bot"],
        )
        self.assertAlmostEqual(record["limits"][2]["percent"], 0.060857)
        self.assertEqual(record["limits"][2]["resetsAt"], "2026-10-30T16:24:17.935000+00:00")

    def test_grok_bot_failure_keeps_cursor_meters(self):
        token = jwt("auth0|user_123")

        def fail(_token):
            raise collect.UsageError(collect.UNREACHABLE)

        record = collect.collect(token=token, fetch=lambda _t: SUMMARY, fetch_grok_bot=fail, clock=lambda: 1000)
        self.assertFalse(record["limitsStale"])
        self.assertEqual([item["title"] for item in record["limits"]], ["Grok/Cursor", "Other models"])

    def test_grok_bot_without_included_limit_is_omitted(self):
        self.assertIsNone(collect.grok_bot_pool({"usagePercent": 0, "hasNonZeroIncludedLimit": False}))
        with self.assertRaises(collect.UsageError):
            collect.grok_bot_pool({"usagePercent": "6"})


if __name__ == "__main__":
    unittest.main()
