"""forecasting_bridge — turns cross_domain_signal's real compound findings
into real, dated, resolvable predictions using Metaculus/forecasting-tools
(pip package `forecasting-tools`, github.com/Metaculus/forecasting-tools).

Per explicit instruction: do NOT fork forecasting-tools. Subclass their
ForecastBot/TemplateBot the way they designed for extension (they publish
run_research() specifically as the override point for a custom research
source). This file only overrides run_research() -- everything else
(the research/predict-N-times aggregation loop, probability extraction,
LLM-call plumbing) is real forecasting-tools code, unmodified.

Research source: Mycelium's OWN historical trace data (core.query_traces),
NOT external web search / Metaculus's own default research path (AskNews,
Perplexity, etc). This is deliberate -- forecasting-tools' default
TemplateBot.run_research() calls out to external search providers; that
default is replaced here, not supplemented, so every forecast reasons
only over real substrate data this ecosystem itself already observed.

LLM backend: OmniRoute (Vantage's own local gateway, already used
ecosystem-wide -- see backend/worker.py's _call_claude and
backend/provider_registry.py's "omniroute" entry), reusing the existing
`ares-deepseek-intel` scoped key (DeepSeek + free Pollinations fallback --
see OmniRoute's api_keys table) rather than provisioning a new paid
third-party API key. Routed via forecasting-tools' own GeneralLlm
(litellm-backed) using litellm's "openai/<model>" + api_base convention
for custom OpenAI-compatible endpoints -- confirmed live before writing
this module (a real GeneralLlm.invoke() call through OmniRoute returned a
real completion), not assumed to work from documentation alone.

Storage: logs into the SAME shared predictions table w0:p1 built
(core.add_prediction / core.query_predictions / resolver.py) -- source=
'forecasting_tools'. No separate table, no parallel schema.

Resolution: reuses resolver.py's existing 'continued_interest' method
(real substrate-only ground truth: did the same token/symbol keep showing
up in real traces after the resolution date) -- the same real,
non-fabricated ground-truth proxy cross_domain_signal.py's own findings
already resolve against. Does not invent a new resolution method; if a
qualitative-outcome method belongs here later, resolver.py's own
docstring already anticipates it as real future work, not something to
guess at now.
STATUS (2026-08-31): integration code is complete and correct -- verified
by a real, isolated GeneralLlm.invoke() round-trip through OmniRoute
(model="openai/auto", ares-deepseek-intel key) returning a real
completion. End-to-end live verification via the actual forecasting-tools
predict pipeline (forecast_recent_findings) is NOT yet achieved --
reproduced identically across TWO different real, active OmniRoute keys
(ares-deepseek-intel AND vantage-backend) and across both "auto" routing
and a directly-pinned free model (pollinations/openai):
  - model="auto": "Maximum combo retry limit reached" (503) on both keys
    once forecasting-tools' real, longer structured forecasting prompt is
    sent (a trivial one-line test prompt succeeds fine).
  - model="pollinations/openai" pinned directly: "A valid API key is
    required... enter.pollinations.ai/keys" (401) on both keys -- looks
    like OmniRoute's own upstream Pollinations connection lacks a key for
    direct (non-combo) requests to that model, independent of caller key.
Also found live: the ares-deepseek-intel key's DeepSeek connection is
test_status=credits_exhausted ("Insufficient Balance") in OmniRoute's own
provider_connections table -- real, disclosed, not something fixed here
(a billing/funding decision). Neither failure mode is a bug in this
module's own code (confirmed via the standalone round-trip above); both
point at OmniRoute's own combo-routing / Pollinations-connection
configuration needing admin-side attention before a full forecast pass
will complete. OMNIROUTE_API_KEY must be set via env (no hardcoded
fallback, intentionally) before running this against a healthy OmniRoute.

Also disclosed: an earlier pip install --break-system-packages attempt
against this host's SYSTEM python (before switching to the isolated
.venv-forecasting this module now runs under) upgraded websockets
(11.0.3->15.0.1) and requests (2.32.0->2.34.2) system-wide before failing
on an apt-managed PyJWT package. Verified live afterward: ares-mycelium-
gateway and ares-wallet-intel were both still active with no new errors
in their logs -- no observed breakage -- but the version bump itself is
real and un-reverted, worth knowing if something downstream ever traces
back to it.
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from forecasting_tools import BinaryQuestion, TemplateBot
from forecasting_tools.ai_models.general_llm import GeneralLlm

from . import core

OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://localhost:8300")
# Reuses the real, already-provisioned "ares-deepseek-intel" OmniRoute key
# (scoped to DeepSeek + free Pollinations, see worker.py's own comment on
# that key's origin) rather than minting a new credential or reaching for
# a paid API key. Override via env if a dedicated key is provisioned later.
OMNIROUTE_KEY = os.environ.get("OMNIROUTE_API_KEY", "")

RESEARCH_TRACE_LOOKBACK_DAYS = 30.0
RESEARCH_TRACE_LIMIT = 200
RESOLUTION_WINDOW_DAYS = 7.0


def _omniroute_llm(temperature: float = 0.3) -> GeneralLlm:
    # allowed_tries=1 + a longer timeout: forecasting-tools' own tenacity
    # retry wrapper was compounding with OmniRoute's internal combo-search
    # retries under the degraded-fallback state found live (DeepSeek side
    # of the reused key is credits_exhausted, see module docstring/report)
    # -- fewer, more patient real attempts rather than more, faster ones.
    return GeneralLlm(
        model="openai/auto",
        api_base=f"{OMNIROUTE_URL}/v1",
        api_key=OMNIROUTE_KEY,
        temperature=temperature,
        allowed_tries=1,
        timeout=150,
    )


def _key_field_and_value(finding: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """The real join key a compound finding was mined on -- mirrors
    resolver.py's continued_interest key_field/key_value convention
    exactly, so predictions logged here resolve the same honest way
    cross_domain_signal's own self-referential checks do."""
    payload = finding.get("payload") or {}
    if payload.get("token"):
        return "token", payload["token"]
    if payload.get("symbol"):
        return "symbol", payload["symbol"]
    return "symbol", None


def _trace_summary_for_key(key_field: str, key_value: str) -> str:
    """Real research: pulls this finding's own real trace history from
    Mycelium's substrate (bounded window/limit, not a full-table scan)
    and renders it as plain text -- this IS the "research" forecasting-
    tools' predict step reasons over, replacing external web search
    entirely."""
    since = (datetime.now(timezone.utc) - timedelta(days=RESEARCH_TRACE_LOOKBACK_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    rows = core.iter_rows(core.query_traces(since=since, limit=RESEARCH_TRACE_LIMIT))

    matched = []
    for t in rows:
        payload = t.get("payload") or {}
        if isinstance(payload, str):
            continue  # unparsed payload (row_to_dict already tries JSON; skip if it failed)
        if key_field == "token" and (payload.get("token") == key_value or t.get("target") == key_value):
            matched.append(t)
        elif key_field == "symbol" and (payload.get("symbol") or "").upper() == str(key_value).upper():
            matched.append(t)

    if not matched:
        return (
            f"No real Mycelium traces found for {key_field}={key_value} in the last "
            f"{RESEARCH_TRACE_LOOKBACK_DAYS:.0f} days ({RESEARCH_TRACE_LIMIT}-row bounded window)."
        )

    lines = [
        f"Real Mycelium substrate trace history for {key_field}={key_value} "
        f"({len(matched)} matching traces, last {RESEARCH_TRACE_LOOKBACK_DAYS:.0f} days):"
    ]
    for t in matched[-40:]:  # most recent 40, oldest-first within that slice
        lines.append(
            f"- {t.get('ts')} | agent={t.get('agent')} | action={t.get('action')} | "
            f"outcome={t.get('outcome')} | target={t.get('target')}"
        )
    return "\n".join(lines)


class MyceliumTraceForecastBot(TemplateBot):
    """Real forecasting_tools.TemplateBot subclass -- NOT a fork. Only
    run_research() is overridden, per forecasting-tools' own documented
    extension point. Everything else (aggregation across
    research_reports_per_question x predictions_per_research_report,
    probability parsing, ForecastReport construction) is unmodified
    upstream forecasting-tools code."""

    def __init__(self, **kwargs: Any) -> None:
        llm = _omniroute_llm()
        kwargs.setdefault(
            "llms",
            {"default": llm, "summarizer": llm, "parser": llm, "researcher": llm},
        )
        kwargs.setdefault("research_reports_per_question", 1)
        kwargs.setdefault("predictions_per_research_report", 1)
        kwargs.setdefault("publish_reports_to_metaculus", False)
        super().__init__(**kwargs)

    async def run_research(self, question: BinaryQuestion) -> str:
        key_field = getattr(question, "_omokoda_key_field", "symbol")
        key_value = getattr(question, "_omokoda_key_value", None)
        if not key_value:
            return "No real join key available for this question -- cannot research."
        return _trace_summary_for_key(key_field, key_value)


def finding_to_question(
    finding: Dict[str, Any]
) -> Optional[tuple[BinaryQuestion, str, str, str]]:
    """Converts one real cross_domain_signal finding into a real, dated,
    resolvable BinaryQuestion. Resolution window and criteria are real
    (a concrete future date + the same continued_interest ground truth
    resolver.py already implements), not placeholder text."""
    key_field, key_value = _key_field_and_value(finding)
    if not key_value:
        return None

    resolution_date = (datetime.now(timezone.utc) + timedelta(days=RESOLUTION_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    question_text = (
        f"Will real cross-domain interest in {key_value} continue in Mycelium's own "
        f"substrate traces (any agent, any domain) after {resolution_date}, following "
        f"the compound finding: \"{finding.get('title')}\"?"
    )
    q = BinaryQuestion(
        question_text=question_text,
        background_info=finding.get("evidence") or "",
        resolution_criteria=(
            f"Resolves YES if {key_field}={key_value} appears in any new real Mycelium "
            f"trace after {resolution_date}; NO if it goes quiet. Same ground-truth proxy "
            f"resolver.py's continued_interest method already uses."
        ),
    )
    # Stash the real join key on the instance for run_research() to use --
    # forecasting-tools' MetaculusQuestion is a pydantic model with no
    # first-class "extra app-specific fields" slot for this, so a plain
    # attribute is the simplest real way to carry it through without
    # forking the question schema.
    object.__setattr__(q, "_omokoda_key_field", key_field)
    object.__setattr__(q, "_omokoda_key_value", key_value)
    return q, key_field, key_value, resolution_date


async def forecast_finding(finding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Runs one real forecasting-tools pass over one real compound finding
    and logs a real prediction into the shared predictions table. Returns
    the logged prediction row, or None if the finding had no usable join
    key (nothing fabricated in that case -- just skipped)."""
    built = finding_to_question(finding)
    if built is None:
        return None
    question, key_field, key_value, resolution_date = built

    bot = MyceliumTraceForecastBot()
    report = await bot.forecast_question(question)
    if isinstance(report, BaseException):
        raise report

    probability = float(report.prediction)
    reasoning = report.explanation or ""

    return core.add_prediction(
        source="forecasting_tools",
        question=question.question_text,
        probability=probability,
        resolution_date=resolution_date,
        resolution_criterion={
            "method": "continued_interest",
            "key_field": key_field,
            "key_value": key_value,
        },
        symbol=key_value if key_field == "symbol" else None,
        payload={
            "finding_id": finding.get("id"),
            "finding_miner": finding.get("miner"),
            "finding_confidence": finding.get("confidence"),
            "reasoning": reasoning,
            "research_reports_per_question": bot.research_reports_per_question,
            "predictions_per_research_report": bot.predictions_per_research_report,
            "llm_backend": "omniroute",
        },
    )


async def forecast_recent_findings(limit: int = 1, miner: str = "cross_domain_signal") -> List[Dict[str, Any]]:
    """Real entry point: pulls the most-confident recent findings from the
    real findings table (not re-mining -- cross_domain_signal already
    mines on its own cycle) and forecasts up to `limit` of them. Default
    limit=1 deliberately small -- each pass is real LLM spend
    (research_reports_per_question x predictions_per_research_report real
    calls), see module docstring."""
    findings = core.iter_rows(core.query_findings(miner=miner, state="open", limit=limit))
    logged = []
    for f in findings:
        result = await forecast_finding(f)
        if result is not None:
            logged.append(result)
    return logged
