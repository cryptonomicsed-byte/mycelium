"""nostr_wire — publishing substrate findings onto the ecosystem's Nostr wire.

`publish.py` already anchors substrate state two ways: a local checkpoint store
and a Gitea push. Both answer "can this box's operator silently rewrite the
record?" Neither answers "can another agent, on another box, read what this
substrate learned?" — and that is the whole point of a stigmergic layer. A
finding no other agent can see is a trace nobody follows.

This is the third channel. A finding travels two ways, deliberately:

  * as a **NIP-AE engram** (`kind:30174`) — the record that the substrate
    observed something, addressable and private;
  * as a **Crucible claim** (`kind:47001`) — the assertion that the pattern is
    real, which other agents can independently check.

The second is the interesting one. A miner's finding is exactly the shape
Crucible was built for: a confident assertion, derived from evidence, that
should not be believed just because it was stated. Crucible discounts agreement
for redundancy, so five copies of this substrate agreeing counts as roughly one
witness rather than five — which is the correct treatment for one miner run on
one box.

## This module does not reimplement the wire contract

`minipae.py` is the Python implementation of the contract for this ecosystem —
BIP-340 signing, NIP-44, canonical NIP-01 serialization, the engram `d`-tag
HMAC, slug validation. There is exactly one per language on purpose: the
contract's failure mode is silent divergence between implementations, so every
extra copy is extra risk.

So this module imports minipae and supplies only what is Mycelium's own: how a
finding maps onto an engram body and a claim, and the `mem/mycelium/` slug
space registered in minipae's `NAMESPACES.md`.

Install minipae on the path (it is a single-file, dependency-free module):

    export PYTHONPATH=/path/to/minipae:$PYTHONPATH
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

# === The shared wire contract, from its owning implementation ===

_MINIPAE_HINT = (
    "minipae is required for the Nostr wire channel. It is the Python "
    "implementation of the ecosystem wire contract; this module deliberately "
    "does not reimplement it. Put it on PYTHONPATH: "
    "export PYTHONPATH=/path/to/minipae:$PYTHONPATH"
)

try:  # pragma: no cover - trivial import guard
    import minipae as _m
except ImportError:  # pragma: no cover
    _m = None


def _minipae():
    """Return the minipae module, or raise with an actionable message.

    Deferred rather than raised at import time so `import mycelium.nostr_wire`
    stays cheap and the rest of the package keeps working on a box that has no
    Nostr channel configured.
    """
    if _m is None:
        raise RuntimeError(_MINIPAE_HINT)
    return _m


# Kind constants are mirrored for readability at the call site. They are owned
# elsewhere -- minipae for 30174, crucible-core for 47001 -- and changing one
# here in isolation would break interoperability silently.
KIND_AGENT_ENGRAM = 30174
KIND_CLAIM = 47001

#: Slug namespace, registered in minipae's NAMESPACES.md before first write.
SLUG_PREFIX = "mem/mycelium"


def normalize_slug_segment(segment: str) -> str:
    """Fold one path segment into minipae's slug grammar.

    `minipae.validate_slug` accepts only ``[a-z0-9_-]`` per segment, at most 64
    bytes each. Miner names and finding titles are free text, so an
    unnormalised segment produces a slug minipae refuses -- and an engram no
    minipae client can address, failing silently in another process rather than
    here.

    Normalising costs nothing that matters: the slug is HMAC'd into the ``d``
    tag before it reaches the wire, so it is an addressing key and never
    display text. The real title travels in the event content.
    """
    import re
    import unicodedata

    decomposed = unicodedata.normalize("NFD", str(segment))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    folded = stripped.casefold()
    folded = re.sub(r"[^a-z0-9_-]+", "-", folded)
    folded = re.sub(r"-{2,}", "-", folded).strip("-")

    # Truncate on bytes, not characters: minipae's 64 is a byte limit.
    while len(folded.encode()) > 64:
        folded = folded[:-1]
    folded = folded.strip("-")

    if not folded:
        raise ValueError(
            f"slug segment {segment!r} normalises to nothing; "
            "it cannot be used as an engram address"
        )
    return folded


def slug_finding(finding_id: str) -> str:
    """Engram slug for one finding. Validated before it is returned."""
    slug = f"{SLUG_PREFIX}/finding/{normalize_slug_segment(finding_id)}"
    if not _minipae().validate_slug(slug):
        raise ValueError(f"built an invalid engram slug: {slug}")
    return slug


def slug_trace(resource: str) -> str:
    """Engram slug for a trace over one resource URI."""
    slug = f"{SLUG_PREFIX}/trace/{normalize_slug_segment(resource)}"
    if not _minipae().validate_slug(slug):
        raise ValueError(f"built an invalid engram slug: {slug}")
    return slug


def finding_record(finding: Dict[str, Any]) -> Dict[str, Any]:
    """The wire body for a finding.

    Flat and self-describing so a Rust, Julia or TypeScript reader can
    interpret it without importing Mycelium. ``payload`` is parsed back from
    its stored JSON string so a reader gets structure rather than an escaped
    blob.
    """
    payload = finding.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": payload}

    return {
        "finding_id": finding.get("id", ""),
        "miner": finding.get("miner", ""),
        "confidence": float(finding.get("confidence", 0.0)),
        "title": finding.get("title", ""),
        "evidence": finding.get("evidence", ""),
        "suggestion": finding.get("suggestion", ""),
        "state": finding.get("state", ""),
        "created_ts": finding.get("created_ts"),
        "payload": payload or {},
    }


def build_finding_engram(
    finding: Dict[str, Any],
    seckey: bytes,
    owner_pubkey: bytes,
) -> Dict[str, Any]:
    """Build a signed NIP-AE engram carrying a finding.

    Content is NIP-44 encrypted and the slug is HMAC'd into the ``d`` tag, both
    by ``minipae.build_event`` -- a relay operator learns that this substrate
    wrote something without learning which resource it concerns.

    The body is passed as a dict, not a pre-serialised string: minipae does its
    own canonical encoding before encrypting, and handing it a string would
    nest one JSON document inside another.
    """
    m = _minipae()
    return m.build_event(
        slug_finding(finding.get("id", "unknown")),
        finding_record(finding),
        seckey,
        owner_pubkey,
    )


def _sign_event(kind: int, content: str, tags: list, seckey: bytes) -> Dict[str, Any]:
    """Assemble and sign an arbitrary-kind event.

    minipae exposes builders for the kinds it owns (engram, auth, relay list)
    but not a generic one, so a Crucible claim is assembled here from its
    primitives -- ``event_id``, ``schnorr_sign``, ``pubkey_from_secret`` -- and
    never from a hand-rolled reimplementation of any of them.

    Field order and the id-then-sign sequence mirror ``minipae.build_event``
    exactly: the signature is over the id, so computing the id from anything
    other than the final field set produces an event that verifies nowhere.
    """
    import secrets
    import time

    m = _minipae()
    pubkey = m.pubkey_from_secret(int.from_bytes(seckey, "big"))
    ev = {
        "kind": kind,
        "pubkey": pubkey.hex(),
        "created_at": int(time.time()),
        "tags": tags,
        "content": content,
    }
    ev["id"] = m.event_id(ev)
    ev["sig"] = m.schnorr_sign(
        bytes.fromhex(ev["id"]), int.from_bytes(seckey, "big"), secrets.token_bytes(32)
    ).hex()
    return ev


def build_finding_claim(
    finding: Dict[str, Any],
    falsifier: str,
    seckey: bytes,
    half_life_secs: int = 86400,
) -> Dict[str, Any]:
    """Build a signed Crucible claim asserting a finding is real.

    Crucible's one rule: an assertion must say how it could be proven wrong.
    ``falsifier`` is the content address of the WASM predicate that returns
    false if the pattern is not there. Crucible rejects a claim without one at
    parse time, so this refuses rather than emitting one that will bounce.

    The miner's own confidence rides along in the content. Crucible prices
    confidence: being wrong at 0.99 costs far more than being wrong at 0.55, so
    a miner that overstates is penalised more than one that hedges honestly.
    """
    if not falsifier:
        raise ValueError(
            "a Crucible claim requires a falsifier; Crucible rejects claims without one"
        )

    record = finding_record(finding)
    content = json.dumps(
        {
            "statement": record["title"],
            "falsifier": falsifier,
            "finding_id": record["finding_id"],
            "miner": record["miner"],
            "confidence": record["confidence"],
            "evidence": record["evidence"],
            "half_life_secs": half_life_secs,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )

    tags = [
        ["falsifier", falsifier],
        ["miner", normalize_slug_segment(record["miner"] or "unknown")],
        ["half_life", str(half_life_secs)],
    ]
    return _sign_event(KIND_CLAIM, content, tags, seckey)


def build_finding_events(
    finding: Dict[str, Any],
    seckey: bytes,
    owner_pubkey: bytes,
    falsifier: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build the signed events for a finding without contacting any relay.

    Returned as ``{"engram": ..., "claim": ...}``, claim present only when a
    falsifier is supplied. Separated from publishing so a caller can inspect,
    batch or route the events, and so this is testable with no network.
    """
    events = {"engram": build_finding_engram(finding, seckey, owner_pubkey)}
    if falsifier:
        events["claim"] = build_finding_claim(finding, falsifier, seckey)
    return events


async def publish_finding(
    finding: Dict[str, Any],
    seckey: bytes,
    owner_pubkey: bytes,
    relay: str,
    falsifier: Optional[str] = None,
    authenticated: bool = True,
) -> Dict[str, Any]:
    """Publish a finding to ``relay`` as an engram, and as a claim if falsifiable.

    Async because ``minipae.publish`` is: it speaks websockets, and the
    substrate's own callers are free to run this on their own loop.

    Returns what the relay actually said, per event, unmodified. A rejection
    stays a rejection -- minipae's own history includes a bug where an
    ``auth-required`` refusal was read as ``ok: True``, and papering over a
    failed write is worse here than in most places, because a finding nobody
    can read is indistinguishable from a finding nobody made.

    ``authenticated`` uses NIP-42, which the production Buzz relay requires for
    writes. Turn it off only for a relay known to accept anonymous writes.
    """
    m = _minipae()
    events = build_finding_events(finding, seckey, owner_pubkey, falsifier)

    results: Dict[str, Any] = {}
    for name, ev in events.items():
        if authenticated:
            results[name] = await m.publish_authenticated(relay, ev, seckey)
        else:
            results[name] = await m.publish(relay, ev)
    return results
