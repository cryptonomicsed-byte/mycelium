"""signal-quality domain — structured prediction extraction + verifiability
scoring for free-text trading calls, ported from HKUDS/AI-Trader's
signal_quality.py (2026-08-29 HKUDS pattern audit: flagged "worth
building" -- a genuinely different axis from wallet-intel's wallet-BEHAVIOR
miners, this scores wallet.py's real trade traces don't have any equivalent
of: how good is a free-text CALL an agent actually posted, not what a
wallet actually did on-chain.

Trace contract (new -- nothing in this codebase emits matching traces yet,
same as wallet-intel's contract had to be established before wallet/
scanner.py existed to feed it):
    kind: "decision" or "observation"
    action: "signal_post"
    payload: {
        title: str (optional), content: str (the actual call text),
        symbol: str (optional), symbols: str comma-separated (optional),
        tags: list[str] (optional),
    }
A future integration point (a Buzz-posted trading call, a GMGN/social
comment feed, a manual /signal-post tool) emits these; this domain mines
whatever's already in the substrate under that shape. See
scripts/demo_seed_signal_quality.py for a real, runnable example.

Algorithm (unchanged from AI-Trader, ported not reinvented -- this is the
"hard-won part" worth reusing exactly, same reasoning that kept
shumei_solver.py's slider-solving math untouched when account_farm.py
generalized the CAPTCHA farm):
    direction/target_price/target_probability/confidence extracted via
    regex + EN keyword matching from the free text (see
    extract_prediction()). Quality is a weighted composite of five
    component scores (see score_signal_quality()):
        verifiability (0.30) -- does the call commit to something checkable
          (a direction, a symbol, a target price/probability)?
        evidence      (0.25) -- length + reasoning-keyword density
          (because/risk/evidence/data/chart/catalyst)
        specificity   (0.20) -- names a symbol/tags, isn't vague
        novelty       (0.15) -- penalized for exact-duplicate content
          within the same mining window (spam/copy-paste detection)
        review        (0.10) -- placeholder for downstream human/agent
          endorsement signal (accepted_reply_id in AI-Trader's schema);
          always 1.0 here since Mycelium traces carry no reply/endorsement
          field yet -- documented as inert rather than fabricated.
    Each clamped to [0, 5]; overall = weighted sum, also clamped to [0, 5].

Unlike wallet-intel's miners, this domain's flagship finding legitimately
IS a per-signal alert (a genuinely well-constructed, checkable call is
worth surfacing on its own), so its alert_condition builder exposes the
real component scores rather than falling back to raw payload.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from . import registry

registry.register_domain(
    "signal-quality",
    "Structured prediction extraction + verifiability scoring for "
    "free-text trading calls (kind in decision/observation, "
    "action='signal_post'). Answers 'how good is this CALL', a different "
    "axis from wallet-intel's on-chain wallet-BEHAVIOR miners -- ported "
    "from HKUDS/AI-Trader's signal_quality.py, ~150 lines of regex + "
    "arithmetic, no ML dependency.",
)

MODEL_VERSION = "heuristic-v1"
MAX_FINDINGS = 5
NOVELTY_FLAG_DUPLICATE_COUNT = 2  # >=N exact-duplicate posts -> a noise finding


def _text(payload: Dict[str, Any]) -> str:
    return " ".join(
        str(payload.get(key) or "") for key in ("title", "content", "symbol", "symbols", "tags")
    ).strip()


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 4)


def extract_prediction(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Structured prediction fields pulled from a signal's free text.
    Regex-based, same shape as AI-Trader's extract_prediction_from_signal
    (EN keywords only here -- the ZH keyword set in the source was for
    HKUDS's own bilingual signal feed, not relevant to anything Mycelium
    ingests today; add back if a real bilingual source ever feeds this)."""
    content = _text(payload)
    lower = content.lower()

    direction: Optional[str] = None
    if any(w in lower for w in ("buy", "long", "bull", "upside", "breakout")):
        direction = "up"
    elif any(w in lower for w in ("sell", "short", "bear", "downside", "breakdown")):
        direction = "down"
    elif any(w in lower for w in ("hold", "neutral", "range", "sideways")):
        direction = "flat"

    price_match = re.search(r"(?:target|tp|price)\D{0,12}([0-9]+(?:\.[0-9]+)?)", content, re.IGNORECASE)
    probability_match = re.search(r"([0-9]{1,3})(?:\s?%|\s?percent)", content, re.IGNORECASE)
    confidence_match = re.search(r"(?:confidence|conf)\D{0,12}([0-9]+(?:\.[0-9]+)?)", content, re.IGNORECASE)

    target_price = float(price_match.group(1)) if price_match else None
    target_probability = None
    if probability_match:
        target_probability = max(0.0, min(float(probability_match.group(1)) / 100.0, 1.0))
    confidence = None
    if confidence_match:
        raw = float(confidence_match.group(1))
        confidence = raw / 100.0 if raw > 1 else raw
        confidence = max(0.0, min(confidence, 1.0))

    symbol = payload.get("symbol") or (str(payload.get("symbols") or "").split(",")[0].strip() or None)

    return {
        "content": content,
        "symbol": symbol,
        "direction": direction,
        "target_price": target_price,
        "target_probability": target_probability,
        "confidence": confidence,
        "keywords": [w for w in ("target", "risk", "because", "evidence") if w in lower],
    }


def score_signal_quality(
    payload: Dict[str, Any],
    duplicate_count: int,
) -> Dict[str, Any]:
    """Five-component weighted quality score, identical weights to
    AI-Trader's score_signal_quality: verifiability*0.3 + evidence*0.25 +
    specificity*0.2 + novelty*0.15 + review*0.1."""
    prediction = extract_prediction(payload)
    content = prediction["content"]
    lower = content.lower()

    verifiability = 1.0
    if prediction["direction"]:
        verifiability += 1.2
    if prediction["symbol"]:
        verifiability += 0.8
    if prediction["target_price"] is not None or prediction["target_probability"] is not None:
        verifiability += 1.2

    evidence = min(
        5.0,
        len(content) / 160.0
        + sum(w in lower for w in ("because", "risk", "evidence", "data", "chart", "catalyst")) * 0.7,
    )
    specificity = (
        1.0
        + (1.0 if payload.get("symbol") or payload.get("symbols") else 0.0)
        + (1.0 if payload.get("tags") else 0.0)
        + min(len(content) / 320.0, 2.0)
    )
    novelty = 5.0 if duplicate_count == 0 else max(0.5, 5.0 - duplicate_count)
    # Inert placeholder, not fabricated: Mycelium traces carry no
    # reply/endorsement field yet (see module docstring).
    review = 1.0
    overall = (verifiability * 0.3) + (evidence * 0.25) + (specificity * 0.2) + (novelty * 0.15) + (review * 0.1)

    return {
        "prediction": prediction,
        "verifiability_score": _clamp_score(verifiability),
        "evidence_score": _clamp_score(evidence),
        "specificity_score": _clamp_score(specificity),
        "novelty_score": _clamp_score(novelty),
        "review_score": _clamp_score(review),
        "overall_score": _clamp_score(overall),
        "duplicate_count": duplicate_count,
        "model_version": MODEL_VERSION,
    }


def _signal_traces(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in traces:
        if t.get("kind") not in ("decision", "observation"):
            continue
        if t.get("action") != "signal_post":
            continue
        payload = t.get("payload") or {}
        if not (payload.get("content") or payload.get("title")):
            continue
        out.append(t)
    return out


def signal_quality(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score every signal_post trace in this window; surface the top
    highest-quality calls (worth an agent's attention -- a well-constructed,
    checkable prediction) as findings, plus a separate finding for any
    exact-duplicate content spam (the "review this before it costs
    reputation" case). Findings are the score itself, not a trade
    recommendation -- this measures how the CALL was made, not whether it
    was right."""
    signals = _signal_traces(traces)
    if not signals:
        return []

    content_counts = Counter((t.get("payload") or {}).get("content", "").strip().lower() for t in signals)

    scored: List[Dict[str, Any]] = []
    for t in signals:
        payload = t.get("payload") or {}
        content_key = str(payload.get("content", "")).strip().lower()
        duplicate_count = max(0, content_counts.get(content_key, 1) - 1)
        result = score_signal_quality(payload, duplicate_count)
        scored.append({
            "agent": t.get("agent"),
            "session": t.get("session"),
            "payload": payload,
            "result": result,
        })

    findings: List[Dict[str, Any]] = []

    top = sorted(scored, key=lambda s: s["result"]["overall_score"], reverse=True)[:MAX_FINDINGS]
    for s in top:
        r = s["result"]
        pred = r["prediction"]
        symbol_txt = f" on {pred['symbol']}" if pred["symbol"] else ""
        direction_txt = f", direction={pred['direction']}" if pred["direction"] else ""
        findings.append({
            "miner": "signal_quality",
            "confidence": r["overall_score"] / 5.0,
            "title": f"High-quality signal from {s['agent']}{symbol_txt} (score {r['overall_score']:.2f}/5)",
            "evidence": (
                f"verifiability={r['verifiability_score']:.2f} evidence={r['evidence_score']:.2f} "
                f"specificity={r['specificity_score']:.2f} novelty={r['novelty_score']:.2f}{direction_txt}"
            ),
            "suggestion": "alert",
            "payload": {"agent": s["agent"], "session": s["session"], **r},
        })

    noisy_content = [content for content, count in content_counts.items() if count - 1 >= NOVELTY_FLAG_DUPLICATE_COUNT and content]
    for content in noisy_content[:MAX_FINDINGS]:
        offenders = sorted({t.get("agent") for t in signals if str((t.get("payload") or {}).get("content", "")).strip().lower() == content})
        count = content_counts[content]
        findings.append({
            "miner": "signal_quality",
            "confidence": min(0.9, 0.5 + 0.1 * count),
            "title": f"Duplicate-content signal spam ({count}x): {content[:60]}",
            "evidence": f"{count} identical-content signal_post traces from {', '.join(offenders)}",
            "suggestion": "alert",
            "payload": {"content": content, "count": count, "agents": offenders},
        })

    return findings


def _signal_quality_alert_condition(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Real per-signal condition (unlike wallet-intel's miners, this
    domain's payload genuinely is an alert-shaped score)."""
    return {
        "metric": "signal_quality_score",
        "min_overall_score": payload.get("overall_score", 0),
        "verifiability_score": payload.get("verifiability_score"),
        "novelty_score": payload.get("novelty_score"),
    }


registry.register_miner("signal-quality", "signal_quality", signal_quality, alert_condition=_signal_quality_alert_condition)
