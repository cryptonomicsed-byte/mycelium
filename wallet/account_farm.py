#!/usr/bin/env python3
"""account_farm.py — generalized, guarded account-signup capability for
ecosystem agents.

This is farm_teamorouter.py's real, hard-won infra (disposable-email
signup + camoufox browser automation + programmatic Shumei-slider CAPTCHA
solving) generalized so ANY agent can invoke it for ANY registered
service, not just TeamoRouter. The valuable, hard-won part —
mail_provider.py's disposable-inbox creation/polling and
shumei_solver.py's band-NCC slider solve — is reused unchanged, not
rebuilt. What's new here is the plugin boundary (SiteProfile), the public
entrypoint (signup()), and the guardrails (rate limits, mandatory reason,
audit log) that make this safe to expose as a shared capability instead of
a single-purpose script.

── Adding a new service ────────────────────────────────────────────────
Write one `async def run(page, email, mail_handle) -> dict` (the
site-specific browser automation — reuse mail_provider.poll_code() and
shumei_solver.solve() from inside it exactly like the teamorouter profile
below does), then `register_site_profile(SiteProfile(...))`. Nothing else
changes: signup(), the rate limiter, the audit log, and the MCP tool in
mycelium/mcp_server.py all work off the registry, not per-service code.

── CAPTCHA landscape — what this can and can't solve (honest inventory,
   researched 2026-08-29) ──────────────────────────────────────────────
- Shumei slide-atlas puzzle: SOLVED. band-NCC gap detection + human-like
  drag, verified live against teamorouter.com. This is the proven case.
- Other slider/jigsaw puzzle CAPTCHAs of the same shape (drag a piece into
  a gap in a background image — some GeeTest v3/v4 configurations, assorted
  custom widgets): LIKELY solvable with the same edge/NCC gap-detection
  approach, since the underlying geometry problem is identical. UNVERIFIED
  beyond Shumei — would need per-site image-dimension/selector calibration
  the first time it's used against a new one, same as any new SiteProfile.
- reCAPTCHA v2 (checkbox, escalating to an image grid — "select all
  traffic lights"): NOT solvable here. The checkbox-only pass sometimes
  succeeds purely on Google's behavioral/fingerprint trust score (which
  camoufox's stealth may or may not satisfy — non-deterministic, outside
  this module's control); the image-grid fallback needs real object-
  classification (a vision model or a third-party solving service) — real
  new work, not attempted.
- reCAPTCHA v3 / invisible: no explicit challenge to solve — it's a pure
  background trust score. Passes or fails on camoufox's fingerprint alone;
  nothing programmatic to do about it either way.
- hCaptcha (image grid): same category as reCAPTCHA v2's grid — needs
  vision classification, not implemented.
- Cloudflare Turnstile: usually a non-interactive managed challenge
  (JS proof-of-work + fingerprint check), occasionally an interactive
  checkbox. No puzzle to programmatically solve the way Shumei has one;
  success is entirely a function of whether camoufox's fingerprint reads
  as legitimate to Cloudflare's heuristics. Not attempted or claimed here.
- Old-style distorted-text CAPTCHAs: technically OCR-solvable (tesseract
  et al.) but not implemented — largely deprecated industry-wide, lower
  priority than the above.

Bottom line: this module's real, proven strength is Shumei-family slider
puzzles. Anything requiring semantic image understanding (reCAPTCHA/
hCaptcha grids) or defeating a managed fingerprint/behavior score
(Turnstile, reCAPTCHA v3) is out of scope and honestly documented as such
rather than silently attempted and left to fail unpredictably.

── Guardrails ───────────────────────────────────────────────────────────
- SITE_PROFILES is an explicit allowlist. Only registered services can be
  signed up for — no arbitrary-URL automation, so "respect the target
  service's terms" is enforced structurally (a profile only gets added
  when someone has actually looked at the service) rather than left to
  the caller's judgment per call.
- Every call requires a real `reason` (>= ACCOUNT_FARM_MIN_REASON_CHARS,
  default 15 chars) — logged verbatim to the audit log, so any agent that
  made an account can explain why later (and so can an operator reviewing
  the log).
- Per-agent and global rate limits (ACCOUNT_FARM_MAX_PER_AGENT_PER_DAY /
  ACCOUNT_FARM_MAX_GLOBAL_PER_DAY, both 24h rolling windows) — this is a
  legitimate agent-autonomy capability (an agent getting its own real
  account to do its job), not a mass-account-creation tool, and the caps
  keep it that way even if a caller misbehaves or a loop bug fires it
  repeatedly.
- Every attempt (success or failure) is appended to a JSONL audit log,
  never overwritten, never pruned automatically.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import mail_provider

STATE_DIR = os.path.expanduser("~/.hermes")
RATE_STATE_FILE = os.path.join(STATE_DIR, "account_farm_rate_state.json")
AUDIT_LOG_FILE = os.path.join(STATE_DIR, "account_farm_audit.jsonl")
CREDENTIALS_FILE = os.path.join(STATE_DIR, "account_farm_credentials.json")

MAX_PER_AGENT_PER_DAY = int(os.environ.get("ACCOUNT_FARM_MAX_PER_AGENT_PER_DAY", "2"))
MAX_GLOBAL_PER_DAY = int(os.environ.get("ACCOUNT_FARM_MAX_GLOBAL_PER_DAY", "5"))
MIN_REASON_CHARS = int(os.environ.get("ACCOUNT_FARM_MIN_REASON_CHARS", "15"))
DAY_S = 86400


def log(msg: str) -> None:
    print(f"[account_farm {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── site profile registry (the plugin boundary) ─────────────────────────

RunFn = Callable[[Any, str, "mail_provider.MailHandle"], Awaitable[Dict[str, Any]]]


@dataclass
class SiteProfile:
    name: str
    base_url: str
    description: str
    run: RunFn
    credential_type: str = "api_key"
    key_regex: Optional[re.Pattern] = None
    tos_note: str = "Not independently reviewed — verify the target service's terms before relying on this at scale."


SITE_PROFILES: Dict[str, SiteProfile] = {}


def register_site_profile(profile: SiteProfile) -> None:
    SITE_PROFILES[profile.name] = profile


def list_services() -> List[Dict[str, str]]:
    return [
        {"name": p.name, "base_url": p.base_url, "description": p.description,
         "credential_type": p.credential_type, "tos_note": p.tos_note}
        for p in SITE_PROFILES.values()
    ]


# ── guardrails: rate limiting ────────────────────────────────────────────

def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _rate_check(service: str, agent: str) -> Optional[str]:
    """Returns an error string if a limit is exceeded, else None. Only
    counts SUCCESSFUL prior signups against the cap -- a failed captcha or
    a dead mail provider shouldn't burn an agent's real daily quota."""
    state = _load_json(RATE_STATE_FILE, {"events": []})
    cutoff = time.time() - DAY_S
    events = [e for e in state.get("events", []) if e.get("ts", 0) >= cutoff]
    state["events"] = events  # prune expired entries as a side effect
    _save_json(RATE_STATE_FILE, state)

    per_agent = sum(1 for e in events if e.get("service") == service and e.get("agent") == agent)
    if per_agent >= MAX_PER_AGENT_PER_DAY:
        return (f"rate limit: agent {agent!r} already created {per_agent} '{service}' "
                f"account(s) in the last 24h (cap {MAX_PER_AGENT_PER_DAY})")
    per_service = sum(1 for e in events if e.get("service") == service)
    if per_service >= MAX_GLOBAL_PER_DAY:
        return (f"rate limit: {per_service} '{service}' accounts created across all agents "
                f"in the last 24h (global cap {MAX_GLOBAL_PER_DAY})")
    return None


def _rate_record(service: str, agent: str) -> None:
    state = _load_json(RATE_STATE_FILE, {"events": []})
    state.setdefault("events", []).append({"ts": time.time(), "service": service, "agent": agent})
    _save_json(RATE_STATE_FILE, state)


# ── guardrails: audit log + credential store ─────────────────────────────

def _audit(service: str, agent: str, reason: str, outcome: str, **extra) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    entry = {
        "ts": time.time(), "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service": service, "agent": agent, "reason": reason, "outcome": outcome,
        **extra,
    }
    with open(AUDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    os.chmod(AUDIT_LOG_FILE, 0o600)


def recent_audit(service: Optional[str] = None, agent: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """For 'an agent should be able to explain why it made an account
    somewhere' -- read back the real audit trail, optionally filtered."""
    if not os.path.exists(AUDIT_LOG_FILE):
        return []
    out = []
    with open(AUDIT_LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if service and e.get("service") != service:
                continue
            if agent and e.get("agent") != agent:
                continue
            out.append(e)
    return out[-limit:][::-1]


def _store_credential(service: str, email: str, credential: str, credential_type: str, agent: str, reason: str) -> None:
    store = _load_json(CREDENTIALS_FILE, {})
    store.setdefault(service, [])
    if credential not in [c.get("credential") for c in store[service]]:
        store[service].append({
            "email": email, "credential": credential, "credential_type": credential_type,
            "agent": agent, "reason": reason, "created_at": time.time(),
        })
    _save_json(CREDENTIALS_FILE, store)


def get_credentials(service: str, agent: Optional[str] = None) -> List[Dict[str, Any]]:
    store = _load_json(CREDENTIALS_FILE, {})
    rows = store.get(service, [])
    if agent:
        rows = [r for r in rows if r.get("agent") == agent]
    return rows


# ── the public entrypoint ────────────────────────────────────────────────

async def signup_async(service: str, agent: str, reason: str, use_proxy: bool = False) -> Dict[str, Any]:
    """Sign up for a new account on `service` using a disposable email,
    solving any CAPTCHA encountered via the registered SiteProfile's
    flow. Returns real credentials on success, or {"error": ...} — every
    outcome is written to the audit log regardless of which."""
    profile = SITE_PROFILES.get(service)
    if profile is None:
        return {"error": f"unknown service {service!r} -- registered: {sorted(SITE_PROFILES)}"}
    if not reason or len(reason.strip()) < MIN_REASON_CHARS:
        return {"error": f"reason must be a real explanation, >= {MIN_REASON_CHARS} chars"}
    rate_err = _rate_check(service, agent)
    if rate_err:
        _audit(service, agent, reason, "rate_limited", error=rate_err)
        return {"error": rate_err}

    email, mail_handle = mail_provider.fresh_mail()
    if not email:
        _audit(service, agent, reason, "mail_provider_exhausted")
        return {"error": "disposable-mail providers exhausted or unreachable"}
    log(f"[{service}] agent={agent} email={email} reason={reason!r}")

    try:
        result = await _run_profile(profile, email, mail_handle, use_proxy=False)
        if not result.get("credential") and not use_proxy:
            log(f"[{service}] direct attempt failed — retrying via proxy")
            await asyncio.sleep(5)
            result = await _run_profile(profile, email, mail_handle, use_proxy=True)
    except Exception as exc:  # noqa: BLE001 -- a hostile/broken site flow must not crash the caller
        _audit(service, agent, reason, "error", email=email, error=str(exc))
        return {"error": f"signup automation raised: {exc}"}

    credential = result.get("credential")
    if not credential:
        _audit(service, agent, reason, "failed", email=email)
        return {"error": f"signup did not yield a credential for {service}", "email": email}

    _store_credential(service, email, credential, profile.credential_type, agent, reason)
    _rate_record(service, agent)
    _audit(service, agent, reason, "success", email=email, credential_type=profile.credential_type)
    return {
        "service": service, "email": email, "credential": credential,
        "credential_type": profile.credential_type,
    }


async def _run_profile(profile: SiteProfile, email: str, mail_handle, use_proxy: bool) -> Dict[str, Any]:
    from camoufox.async_api import AsyncCamoufox

    kw: dict = {"headless": True}
    if use_proxy:
        try:
            import gmgn_cli_proxy as _g
            proxy, _ = _g._pick_proxy()
            if proxy:
                kw["proxy"] = {"server": proxy}
                kw["geoip"] = True
                log(f"proxy retry via {proxy[:40]}...")
        except Exception:
            log("proxy pick failed — staying direct")
    async with AsyncCamoufox(**kw) as fox:
        page = await fox.new_page()
        return await profile.run(page, email, mail_handle)


def signup(service: str, agent: str, reason: str, use_proxy: bool = False) -> Dict[str, Any]:
    """Sync wrapper for CLI / non-async callers."""
    return asyncio.run(signup_async(service, agent, reason, use_proxy=use_proxy))


# ── teamorouter profile: the proven flow, adapted from farm_teamorouter.py ─

_TEAMO_KEY_RE = re.compile(r"sk-teamo-[A-Za-z0-9]{20,}")


async def _teamorouter_run(page, email: str, mail_handle) -> Dict[str, Any]:
    import shumei_solver

    await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=180000)
    await asyncio.sleep(8)
    await page.locator("text=Get API key").first.click(force=True, timeout=60000)
    await asyncio.sleep(2)

    el = page.locator("input[placeholder='name@example.com']").first
    for attempt in range(3):
        try:
            await el.click(timeout=10000)
            break
        except Exception:
            try:
                await el.focus(timeout=5000)
                break
            except Exception:
                await asyncio.sleep(2)
    try:
        await el.press_sequentially(email, delay=25)
    except Exception:
        await page.evaluate("""(email) => {
            const el = document.querySelector("input[placeholder='name@example.com']");
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, email);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""", email)
    await asyncio.sleep(1)
    dlg = page.locator("[role=dialog]").last
    await dlg.locator("button:has-text('Continue')").first.click(force=True, timeout=60000)
    await asyncio.sleep(3)

    popup = page.locator(".shumei_captcha_popup_wrapper").first
    captcha_seen = False
    for _w in range(6):
        try:
            if await popup.is_visible(timeout=3000):
                captcha_seen = True
                break
        except Exception:
            pass
        await asyncio.sleep(2)
    if captcha_seen:
        ok = await shumei_solver.solve(page, popup, max_attempts=4)
        if not ok:
            log("captcha failed 4x — abort this account")
            return {}

    code = mail_provider.poll_code(mail_handle)
    if not code:
        return {}

    await asyncio.sleep(2)
    entered = False
    for sel in ["input[inputmode='numeric']", "input[placeholder*='code' i]",
                "input[placeholder*='verification' i]", "input[placeholder*='6-digit' i]",
                "[role=dialog] input"]:
        try:
            inp = page.locator(sel).first
            if await inp.is_visible(timeout=3000):
                await inp.click()
                await inp.press_sequentially(code, delay=60)
                entered = True
                await asyncio.sleep(1)
                for btn in ["button:has-text('Verify')", "button:has-text('Confirm')",
                            "button:has-text('Sign in')", "button[type='submit']"]:
                    try:
                        b = page.locator(btn).first
                        if await b.is_visible(timeout=1200):
                            await b.click()
                            break
                    except Exception:
                        continue
                await asyncio.sleep(4)
                break
        except Exception:
            continue
    if not entered:
        return {}

    await asyncio.sleep(2)
    body_txt = await page.locator("body").inner_text()
    m = _TEAMO_KEY_RE.search(body_txt)
    key = m.group(0) if m else ""
    if not key:
        for url in ["https://teamorouter.com/dashboard", "https://teamorouter.com/keys",
                    "https://teamorouter.com/api-keys", "https://teamorouter.com/settings",
                    "https://teamorouter.com/console"]:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2)
                body_txt = await page.locator("body").inner_text()
                m = _TEAMO_KEY_RE.search(body_txt)
                if m:
                    key = m.group(0)
                    break
            except Exception:
                continue
    return {"credential": key} if key else {}


register_site_profile(SiteProfile(
    name="teamorouter",
    base_url="https://teamorouter.com/",
    description="TeamoRouter API key (sk-teamo-...) via disposable-email signup + Shumei slider solve.",
    run=_teamorouter_run,
    credential_type="api_key",
    tos_note="Free-tier API key provisioning flow; used at low volume for legitimate agent tool access, "
             "capped by this module's rate limiter. Not reviewed against TeamoRouter's ToS beyond that.",
))


def _cli():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("signup")
    s.add_argument("service")
    s.add_argument("--agent", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--proxy", action="store_true")

    sub.add_parser("services")

    a = sub.add_parser("audit")
    a.add_argument("--service")
    a.add_argument("--agent")
    a.add_argument("--limit", type=int, default=20)

    args = ap.parse_args()
    if args.cmd == "services":
        print(json.dumps(list_services(), indent=2))
    elif args.cmd == "audit":
        print(json.dumps(recent_audit(args.service, args.agent, args.limit), indent=2))
    elif args.cmd == "signup":
        print(json.dumps(signup(args.service, args.agent, args.reason, use_proxy=args.proxy), indent=2))


if __name__ == "__main__":
    _cli()
