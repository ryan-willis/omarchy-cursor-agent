#!/usr/bin/env python3

import argparse
import base64
import hashlib
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AGENT_ID = "cursor"
ORIGIN = "https://cursor.com"
SUMMARY_PATH = "/api/usage-summary"
GROK_BOT_PATH = "/api/dashboard/get-sand-usage-status"
GROK_BOT_TITLE = "Grok Bot"
POOLS = (
    ("autoPercentUsed", "Grok/Cursor"),
    ("apiPercentUsed", "Other models"),
)
PLANS = {
    "pro_plus": "Pro+",
    "pro": "Pro",
    "ultra": "Ultra",
    "free": "Free",
    "hobby": "Hobby",
    "business": "Business",
    "team": "Team",
    "enterprise": "Enterprise",
}
PROBE_REUSE_SECONDS = 60
MAX_BODY = 256 * 1024
SIGN_IN = "Sign in with cursor-agent, or the Cursor app, to show plan usage."
REJECTED = "Cursor rejected the session. Sign in again."
UNREACHABLE = "Could not reach Cursor."
UNUSABLE = "Cursor returned an unusable usage response."


class UsageError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UsageError(UNUSABLE)


def state_root():
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))


def cache_root():
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))


def config_root():
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def usage_path():
    return state_root() / "omarchy" / "agents" / "usage" / "cursor.json"


def summary_cache_path():
    return cache_root() / "omarchy" / "agent-usage" / "cursor-summary.json"


def grok_bot_cache_path():
    return cache_root() / "omarchy" / "agent-usage" / "cursor-grok-bot.json"


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def jwt_user(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        user = str(claims["sub"]).split("|")[-1]
    except (AttributeError, IndexError, KeyError, ValueError, TypeError):
        raise UsageError(SIGN_IN) from None
    if not user or len(user) > 128 or any(c in user for c in "<>:/\\ \n"):
        raise UsageError(SIGN_IN)
    return user


def token_from_auth(path):
    data = read_json(path)
    token = data.get("accessToken") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token.count(".") == 2 else ""


def token_from_desktop(path):
    if not path.is_file():
        return ""
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            row = db.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("cursorAuth/accessToken",),
            ).fetchone()
    except (sqlite3.Error, OSError):
        return ""
    token = row[0] if row else ""
    if isinstance(token, bytes):
        token = token.decode("utf-8", "ignore")
    return token if isinstance(token, str) and token.count(".") == 2 else ""


def load_token():
    config = config_root()
    for candidate in (
        token_from_auth(config / "cursor" / "auth.json"),
        token_from_desktop(config / "Cursor" / "User" / "globalStorage" / "state.vscdb"),
    ):
        if candidate:
            return candidate
    raise UsageError(SIGN_IN)


def session_cookie(token):
    return urllib.parse.quote(jwt_user(token) + "::" + token, safe="")


def fetch_json(token, path, body=None):
    headers = {
        "Cookie": "WorkosCursorSessionToken=" + session_cookie(token),
        "Accept": "application/json",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/dashboard",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        ORIGIN + path,
        data=data,
        headers=headers,
        method="GET" if data is None else "POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(MAX_BODY + 1)
    except UsageError:
        raise
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise UsageError(REJECTED) from None
        raise UsageError(UNUSABLE) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UsageError(UNREACHABLE) from None
    if len(raw) > MAX_BODY or token.encode() in raw:
        raise UsageError(UNUSABLE)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise UsageError(UNUSABLE) from None
    if not isinstance(payload, dict):
        raise UsageError(UNUSABLE)
    return payload


def fetch_summary(token):
    return fetch_json(token, SUMMARY_PATH)


def fetch_grok_bot_status(token):
    return fetch_json(token, GROK_BOT_PATH, {})


def token_key(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def cached_json(path, fetcher, token, force, clock):
    cache = read_json(path) or {}
    age = clock() - float(cache.get("at") or 0)
    if not force and cache.get("key") == token_key(token) and 0 <= age < PROBE_REUSE_SECONDS:
        summary = cache.get("summary")
        if isinstance(summary, dict):
            return summary
    summary = fetcher(token)
    write_json(path, {"key": token_key(token), "at": clock(), "summary": summary})
    return summary


def cached_summary(token, force, clock):
    return cached_json(summary_cache_path(), fetch_summary, token, force, clock)


def cached_grok_bot_status(token, force, clock):
    return cached_json(grok_bot_cache_path(), fetch_grok_bot_status, token, force, clock)


def plan_label(membership):
    return PLANS.get(str(membership or "").strip().lower(), "")


def reset_at(value):
    if not isinstance(value, str) or len(value) > 40:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def fraction(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageError(UNUSABLE)
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10000:
        raise UsageError(UNUSABLE)
    return number / 100.0


def plan_bucket(summary):
    for key in ("individualUsage", "teamUsage"):
        block = summary.get(key)
        plan = block.get("plan") if isinstance(block, dict) else None
        if not isinstance(plan, dict) or plan.get("enabled") is False:
            continue
        if any(plan.get(field) is not None for field, _title in POOLS):
            return plan
    return None


def pools(summary):
    if not isinstance(summary, dict):
        raise UsageError(UNUSABLE)
    if summary.get("isUnlimited") is True:
        return []
    plan = plan_bucket(summary)
    if plan is None:
        raise UsageError(UNUSABLE)
    reset = reset_at(summary.get("billingCycleEnd"))
    limits = []
    for field, title in POOLS:
        if plan.get(field) is None:
            continue
        limits.append({
            "label": title,
            "title": title,
            "percent": fraction(plan[field]),
            "resetsAt": reset,
        })
    if not limits:
        raise UsageError(UNUSABLE)
    return limits


def grok_bot_pool(status):
    if not isinstance(status, dict) or status.get("hasNonZeroIncludedLimit") is False:
        return None
    if status.get("usagePercent") is None:
        return None
    return {
        "label": GROK_BOT_TITLE,
        "title": GROK_BOT_TITLE,
        "percent": fraction(status["usagePercent"]),
        "resetsAt": reset_at(status.get("nextResetTimestampUtc")),
    }


def grok_bot_limits(token, force, clock, fetch_grok_bot):
    # Grok Bot is optional: any failure leaves the Cursor meters untouched.
    try:
        if fetch_grok_bot:
            status = fetch_grok_bot(token)
        else:
            status = cached_grok_bot_status(token, force, clock)
        pool = grok_bot_pool(status)
    except UsageError:
        return []
    return [pool] if pool else []


def base_record():
    return {
        "schemaVersion": 1,
        "id": AGENT_ID,
        "name": "Cursor",
        "scope": "account",
        "ready": False,
        "tierLabel": "",
        "usageStatusText": "",
        "authHelpText": "",
        "limits": [],
        "limitsStale": False,
        "limitsFetchedAt": 0,
        "hasLocalStats": False,
        "hasPromptStats": False,
        "updatedAt": "",
    }


def previous_record():
    record = read_json(usage_path()) or {}
    return record if record.get("id") == AGENT_ID else {}


def apply_failure(record, previous, message):
    kept = previous.get("limits") if isinstance(previous.get("limits"), list) else []
    record["updatedAt"] = datetime.now(timezone.utc).isoformat()
    record["ready"] = True
    if kept:
        record["limits"] = kept
        record["tierLabel"] = plan_label(previous.get("tierLabel")) or (
            previous.get("tierLabel") if previous.get("tierLabel") in PLANS.values() else ""
        )
        record["limitsStale"] = True
        record["limitsFetchedAt"] = int(previous.get("limitsFetchedAt") or 0)
        record["usageStatusText"] = "Cursor limits unavailable"
        record["authHelpText"] = message
        return record
    record["tierLabel"] = "Signed out" if message == SIGN_IN else "Cursor"
    record["usageStatusText"] = "Cursor unavailable"
    record["authHelpText"] = message
    return record


def collect(force=False, fetch=None, token=None, clock=None, fetch_grok_bot=None):
    clock = clock or time.time
    record = base_record()
    previous = previous_record()
    try:
        token = load_token() if token is None else token
        if not token:
            raise UsageError(SIGN_IN)
        summary = fetch(token) if fetch else cached_summary(token, force, clock)
        limits = pools(summary)
        if fetch is None or fetch_grok_bot is not None:
            limits = limits + grok_bot_limits(token, force, clock, fetch_grok_bot)
        record["limits"] = limits
        record["tierLabel"] = plan_label(summary.get("membershipType")) or "Cursor"
        if summary.get("isUnlimited") is True and record["tierLabel"]:
            record["tierLabel"] = record["tierLabel"] + " unlimited"
        record["ready"] = True
        record["limitsStale"] = False
        record["limitsFetchedAt"] = int(clock() * 1000)
        record["usageStatusText"] = ""
        record["authHelpText"] = ""
        record["updatedAt"] = datetime.now(timezone.utc).isoformat()
    except UsageError as error:
        apply_failure(record, previous, str(error))
    write_json(usage_path(), record)
    return record


def main(argv):
    parser = argparse.ArgumentParser(description="Write Cursor plan usage for the Omarchy Agents panel.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        collect(force=args.force)
    except Exception:
        print("cursor usage collector failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
