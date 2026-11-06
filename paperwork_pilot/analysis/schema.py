"""The shape of an analysis result, plus a normalizer.

Whatever the AI (or the rules engine) produces gets pushed through
`normalize()` so the browser ALWAYS receives the same clean structure.
Small local models sometimes forget keys or return strings where lists
are expected; the normalizer forgives all of that.
"""

from __future__ import annotations

from typing import Any

DOC_TYPES = {
    "lease": "Apartment / rental lease",
    "medical_bill": "Medical bill",
    "insurance": "Insurance letter (or Explanation of Benefits)",
    "job_offer": "Job offer",
    "terms": "Terms of service / contract",
    "debt": "Debt collection letter",
    "utility": "Utility or phone bill",
    "government": "Government or tax letter",
    "legal": "Legal notice",
    "other": "Document",
}

SEVERITIES = ("high", "medium", "low")
URGENCIES = ("urgent", "soon", "later", "none")

EMPTY_RESULT: dict[str, Any] = {
    "document_type": "other",
    "document_type_label": DOC_TYPES["other"],
    "title": "Your document",
    "plain_summary": "",
    "who_sent_it": "",
    "what_they_want": "",
    "key_facts": [],          # [{"label": str, "value": str}]
    "deadlines": [],          # [{"date": str, "what": str, "urgency": str}]
    "money": [],              # [{"amount": str, "what": str, "recurring": bool}]
    "watch_outs": [],         # [{"title": str, "why": str, "quote": str, "severity": str}]
    "your_options": [],       # [str]
    "questions_to_ask": [],   # [str]
    "next_steps": [],         # [str]
    "glossary": [],           # [{"term": str, "meaning": str}]
    "confidence": "medium",   # high | medium | low
    "notes": [],              # strings shown as gentle warnings to the user
}


def _s(v: Any, limit: int = 2000) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return str(v)[:limit]
    return str(v).strip()[:limit]


def _list(v: Any) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v]
    if isinstance(v, str):
        # allow "a; b; c" or newline separated strings
        parts = [p.strip(" -•\t") for p in v.replace(";", "\n").split("\n")]
        return [p for p in parts if p]
    return [v]


def _str_list(v: Any, limit: int = 12) -> list[str]:
    out = []
    for item in _list(v):
        if isinstance(item, dict):
            text = _s(item.get("text") or item.get("option") or item.get("step") or item.get("question") or "")
            text = text or " — ".join(_s(x) for x in item.values() if x)
        else:
            text = _s(item)
        if text:
            out.append(text[:500])
    return out[:limit]


def _pairs(v: Any, k1: str, k2: str, limit: int = 15) -> list[dict]:
    out = []
    for item in _list(v):
        if isinstance(item, dict):
            a = _s(item.get(k1) or item.get("label") or item.get("name") or item.get("term") or "")
            b = _s(item.get(k2) or item.get("value") or item.get("meaning") or item.get("definition") or "")
        elif isinstance(item, str) and ":" in item:
            a, b = (p.strip() for p in item.split(":", 1))
        else:
            a, b = "", _s(item)
        if a or b:
            out.append({k1: a[:200], k2: b[:600]})
    return out[:limit]


def _choice(v: Any, allowed: tuple, default: str) -> str:
    v = _s(v).lower()
    return v if v in allowed else default


def normalize(raw: dict | None) -> dict:
    """Never raises: on any surprise it returns the best partial result it can."""
    try:
        return _normalize(raw)
    except Exception:  # noqa: BLE001
        safe = {}
        for k in ("document_type", "title", "plain_summary", "who_sent_it", "what_they_want"):
            if isinstance(raw, dict) and isinstance(raw.get(k), str):
                safe[k] = raw[k]
        try:
            return _normalize(safe)
        except Exception:  # noqa: BLE001
            return {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY_RESULT.items()}


def _normalize(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    r: dict[str, Any] = {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY_RESULT.items()}

    dtype = _s(raw.get("document_type")).lower().replace(" ", "_")
    if dtype not in DOC_TYPES:
        # map a few synonyms
        if "lease" in dtype or "rent" in dtype:
            dtype = "lease"
        elif "medical" in dtype or "hospital" in dtype or "doctor" in dtype:
            dtype = "medical_bill"
        elif "insur" in dtype or "eob" in dtype or "claim" in dtype:
            dtype = "insurance"
        elif "offer" in dtype or "employ" in dtype:
            dtype = "job_offer"
        elif "term" in dtype or "contract" in dtype or "agreement" in dtype:
            dtype = "terms"
        elif "debt" in dtype or "collect" in dtype:
            dtype = "debt"
        elif "util" in dtype or "phone" in dtype or "electric" in dtype:
            dtype = "utility"
        elif "tax" in dtype or "irs" in dtype or "gov" in dtype:
            dtype = "government"
        elif "legal" in dtype or "court" in dtype or "notice" in dtype:
            dtype = "legal"
        else:
            dtype = "other"
    r["document_type"] = dtype
    r["document_type_label"] = DOC_TYPES[dtype]

    r["title"] = _s(raw.get("title"), 120) or DOC_TYPES[dtype]
    r["plain_summary"] = _s(raw.get("plain_summary") or raw.get("summary"))
    r["who_sent_it"] = _s(raw.get("who_sent_it") or raw.get("sender"), 300)
    r["what_they_want"] = _s(raw.get("what_they_want") or raw.get("purpose"), 600)

    r["key_facts"] = _pairs(raw.get("key_facts"), "label", "value")

    deadlines = []
    for item in _list(raw.get("deadlines")):
        if isinstance(item, dict):
            deadlines.append(
                {
                    "date": _s(item.get("date") or item.get("when"), 80),
                    "what": _s(item.get("what") or item.get("description") or item.get("event"), 400),
                    "urgency": _choice(item.get("urgency"), URGENCIES, "soon"),
                }
            )
        elif isinstance(item, str) and item.strip():
            deadlines.append({"date": "", "what": item.strip()[:400], "urgency": "soon"})
    r["deadlines"] = deadlines[:15]

    money = []
    for item in _list(raw.get("money")):
        if isinstance(item, dict):
            rec = item.get("recurring")
            if isinstance(rec, str):
                rec = rec.lower() in ("true", "yes", "monthly", "weekly", "yearly", "annual", "recurring")
            money.append(
                {
                    "amount": _s(item.get("amount") or item.get("value"), 60),
                    "what": _s(item.get("what") or item.get("description") or item.get("for"), 400),
                    "recurring": bool(rec),
                }
            )
        elif isinstance(item, str) and item.strip():
            money.append({"amount": "", "what": item.strip()[:400], "recurring": False})
    r["money"] = money[:20]

    watch = []
    for item in _list(raw.get("watch_outs") or raw.get("red_flags") or raw.get("warnings")):
        if isinstance(item, dict):
            watch.append(
                {
                    "title": _s(item.get("title") or item.get("issue") or item.get("name"), 150),
                    "why": _s(item.get("why") or item.get("why_it_matters") or item.get("explanation") or item.get("description"), 700),
                    "quote": _s(item.get("quote") or item.get("text") or item.get("evidence"), 500),
                    "severity": _choice(item.get("severity") or item.get("level"), SEVERITIES, "medium"),
                }
            )
        elif isinstance(item, str) and item.strip():
            watch.append({"title": item.strip()[:150], "why": "", "quote": "", "severity": "medium"})
    # high first
    order = {"high": 0, "medium": 1, "low": 2}
    watch.sort(key=lambda w: order[w["severity"]])
    r["watch_outs"] = watch[:15]

    r["your_options"] = _str_list(raw.get("your_options") or raw.get("options") or raw.get("rights"))
    r["questions_to_ask"] = _str_list(raw.get("questions_to_ask") or raw.get("questions"))
    r["next_steps"] = _str_list(raw.get("next_steps") or raw.get("suggested_next_steps") or raw.get("actions"))
    r["glossary"] = _pairs(raw.get("glossary") or raw.get("terms_explained"), "term", "meaning")
    r["confidence"] = _choice(raw.get("confidence"), ("high", "medium", "low"), "medium")
    r["notes"] = _str_list(raw.get("notes"), 8)
    return r
