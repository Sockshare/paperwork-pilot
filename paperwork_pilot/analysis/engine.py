"""The orchestrator: decides between AI and rules, handles long documents, never crashes the UI."""

from __future__ import annotations

import logging
import time

from ..config import Settings
from ..llm import ProviderError, extract_json, get_provider
from . import prompts
from .rules import BASIC_NOTE, analyze_rules, merge_safety_net
from .schema import normalize

log = logging.getLogger("paperwork_pilot.engine")

# A 3B model with an 8k context handles ~14k characters of document comfortably.
SINGLE_PASS_CHARS = 14_000
CHUNK_CHARS = 9_000
MAX_CHUNKS = 6          # beyond this we summarise the first ~54k chars; keeps worst-case time bounded
TOTAL_BUDGET_SECONDS = 15 * 60  # give up on the AI (and show the quick scan) after this long

REPLY_GOALS = {
    "dispute": "Dispute a charge or decision and ask them to fix it",
    "question": "Ask for a clear explanation of something in the document",
    "more_time": "Ask for more time (an extension) to pay or respond",
    "cancel": "Cancel a service, subscription, or agreement",
    "negotiate": "Negotiate better terms (price, dates, conditions)",
    "payment_plan": "Ask for a payment plan or financial help",
    "appeal": "Appeal a denial and ask them to reconsider",
    "confirm": "Confirm an agreement in writing so there is a record",
    "repairs": "Request repairs or action they are responsible for",
    "validate": "Ask a debt collector to prove (validate) the debt",
}
REPLY_TONES = {
    "polite": "warm and polite",
    "firm": "firm and businesslike",
    "formal": "formal and official",
}


def _chunks(text: str) -> list[str]:
    chunks, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > CHUNK_CHARS and buf:
            chunks.append(buf)
            buf = ""
        buf += para + "\n"
    if buf.strip():
        chunks.append(buf)
    return chunks[:MAX_CHUNKS]


def analyze_quick(text: str) -> dict:
    """Instant rules-only result. Shown first; the AI result replaces it when ready."""
    started = time.time()
    result = analyze_rules(text)
    result["meta"] = {"engine": "offline", "model": "rules-engine", "local": True, "fallback": False, "quick": True, "seconds": round(time.time() - started, 2)}
    return result


def analyze(text: str, settings: Settings) -> dict:
    """Returns a normalized result plus 'meta' describing how it was produced."""
    started = time.time()
    rules = analyze_rules(text)
    provider = get_provider(settings)
    meta = {"engine": provider.name, "model": provider.model, "local": provider.local, "fallback": False, "seconds": 0.0}

    if provider.name == "offline":
        result = rules
        meta["seconds"] = round(time.time() - started, 1)
        return {**result, "meta": meta}

    try:
        if len(text) <= SINGLE_PASS_CHARS:
            raw = provider.complete(prompts.ANALYZE_SYSTEM, prompts.ANALYZE_USER.format(document=text), want_json=True)
            parsed = extract_json(raw)
        else:
            parsed = _analyze_long(text, provider)
        if not parsed or not str(parsed.get("plain_summary", "")).strip():
            raise ProviderError("The AI answered, but not in a form the app could use.")
        result = merge_safety_net(normalize(parsed), rules)
    except ProviderError as exc:
        log.warning("AI failed, using rules: %s", exc)
        result = _fallback(rules, f"The AI had a problem: {exc} Showing the basic scan instead.")
        meta["fallback"] = True
        meta["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected analysis error")
        result = _fallback(rules, "Something unexpected went wrong with the AI, so this is the basic scan.")
        meta["fallback"] = True
        meta["error"] = f"{type(exc).__name__}: {exc}"

    meta["seconds"] = round(time.time() - started, 1)
    return {**result, "meta": meta}


def _fallback(rules: dict, note: str) -> dict:
    """Rules result dressed for 'the AI was on but failed' (no 'turn on AI' nag)."""
    result = dict(rules)
    result["plain_summary"] = result["plain_summary"].replace(" " + BASIC_NOTE, "").replace(BASIC_NOTE, "").strip()
    result["notes"] = [note] + [n for n in result["notes"] if "Basic scan" not in n]
    return result


def strip_basic_note(result: dict) -> dict:
    """Used for the instant result when an AI upgrade is on its way."""
    result = dict(result)
    result["plain_summary"] = result["plain_summary"].replace(" " + BASIC_NOTE, "").replace(BASIC_NOTE, "").strip()
    result["notes"] = [n for n in result["notes"] if "Basic scan" not in n]
    return result


def _analyze_long(text: str, provider) -> dict | None:
    chunks = _chunks(text)
    notes = []
    deadline = time.time() + TOTAL_BUDGET_SECONDS
    for i, chunk in enumerate(chunks, 1):
        if time.time() > deadline:
            raise ProviderError("This document is taking too long for the AI on this computer. Try a smaller model or a cloud key.")
        raw = provider.complete(
            prompts.CHUNK_SYSTEM,
            prompts.CHUNK_USER.format(index=i, total=len(chunks), chunk=chunk),
            want_json=True,
        )
        parsed = extract_json(raw) or {}
        notes.append(f"--- Part {i} ---\n{_compact(parsed)}")
    merged_raw = provider.complete(
        prompts.ANALYZE_SYSTEM,
        prompts.MERGE_USER.format(first_page=text[:3000], notes="\n".join(notes)[:20000]),
        want_json=True,
    )
    final = extract_json(merged_raw)
    if final is None:
        return None
    # Union lists from chunks in case the merge dropped something.
    for key in ("deadlines", "money", "watch_outs", "key_facts", "glossary"):
        final.setdefault(key, [])
        if not isinstance(final[key], list):
            final[key] = []
    return final


def _compact(d: dict) -> str:
    import json

    return json.dumps(d, ensure_ascii=False)[:4000]


def write_reply(result: dict, goal: str, tone: str, details: str, kind: str, settings: Settings) -> dict:
    goal_text = REPLY_GOALS.get(goal, goal or "Respond to this document")
    tone_text = REPLY_TONES.get(tone, "polite")
    kind = "email" if kind == "email" else "letter"
    facts = "; ".join(f"{f['label']}: {f['value']}" for f in result.get("key_facts", [])[:8])
    facts += "; " + "; ".join(f"{m['amount']} {m['what'][:60]}" for m in result.get("money", [])[:4])
    facts += "; " + "; ".join(f"{d['date']} {d['what'][:60]}" for d in result.get("deadlines", [])[:4])

    provider = get_provider(settings)
    if provider.name != "offline":
        try:
            text = provider.complete(
                prompts.REPLY_SYSTEM,
                prompts.REPLY_USER.format(
                    kind=kind,
                    goal=goal_text,
                    tone=tone_text,
                    details=details.strip() or "none",
                    summary=result.get("plain_summary", ""),
                    sender=result.get("who_sent_it") or "the sender",
                    facts=facts.strip("; "),
                ),
            )
            return {"text": text.strip(), "engine": provider.name, "fallback": False}
        except ProviderError as exc:
            log.warning("Reply AI failed: %s", exc)
            return {"text": _template_reply(result, goal, tone, details, kind), "engine": "template", "fallback": True, "error": str(exc)}
    return {"text": _template_reply(result, goal, tone, details, kind), "engine": "template", "fallback": False}


def _template_reply(result: dict, goal: str, tone: str, details: str, kind: str) -> str:
    sender = result.get("who_sent_it") or "[Company name]"
    title = result.get("title") or "your recent document"
    greeting = "Hello," if tone == "polite" else "To whom it may concern,"
    ask = {
        "dispute": "I am writing to dispute the charge/decision described in this document. I do not believe it is correct, and I ask that you review it and correct it.",
        "question": "I am writing to ask for a clear explanation of this document. Some parts are not clear to me.",
        "more_time": "I am writing to request more time to respond/pay. I would appreciate an extension of [number] days.",
        "cancel": "I am writing to cancel my service/agreement, effective as soon as possible. Please confirm the cancellation and the final amount owed, if any, in writing.",
        "negotiate": "I am writing to discuss the terms described in this document. I would like to propose the following changes: [your proposal].",
        "payment_plan": "I am writing to ask about a payment plan or financial assistance for the amount described in this document.",
        "appeal": "I am writing to formally appeal the decision described in this document and ask that you reconsider.",
        "confirm": "I am writing to confirm, in writing, what we discussed: [what was agreed].",
        "repairs": "I am writing to request that the following issue be fixed: [describe the problem]. Please let me know when this will be done.",
        "validate": "I am writing to request validation of this debt. Please send written proof that the debt is real, the amount is correct, and that you have the right to collect it. Until then, please stop collection activity.",
    }.get(goal, "I am writing about this document and would like your help.")
    lines = [f"- {f['label']}: {f['value']}" for f in result.get("key_facts", [])[:5]]
    for m in result.get("money", [])[:2]:
        lines.append(f"- Amount mentioned: {m['amount']} ({m['what'][:70]})")
    for d in result.get("deadlines", [])[:2]:
        lines.append(f"- Date mentioned: {d['date']} ({d['what'][:70]})")
    facts = "\n".join(lines)
    extra = f"\n\nAdditional details:\n{details.strip()}" if details.strip() else ""
    closing = "Please reply in writing by [date], about two weeks from today.\n\nThank you,\n[Your name]\n[Your address or account number]\n[Your phone or email]"
    header = f"Subject: Regarding {title}\n\n" if kind == "email" else f"[Date]\n\n{sender}\n[Their address]\n\n"
    body = f"{header}{greeting}\n\nMy name is [Your name], and I am writing about {title.lower()}"
    if sender != "[Company name]":
        body += f" from {sender}"
    body += f".\n\n{ask}"
    if facts:
        body += f"\n\nFor reference:\n{facts}"
    body += f"{extra}\n\n{closing}"
    return body


def ask_question(text: str, result: dict, question: str, settings: Settings) -> dict:
    question = question.strip()
    if not question:
        return {"answer": "Type a question first.", "engine": "none"}
    provider = get_provider(settings)
    if provider.name == "offline":
        return {"answer": _offline_answer(text, result, question), "engine": "offline"}
    try:
        answer = provider.complete(prompts.ASK_SYSTEM, prompts.ASK_USER.format(document=text[:SINGLE_PASS_CHARS], question=question))
        return {"answer": answer.strip(), "engine": provider.name}
    except ProviderError as exc:
        return {"answer": _offline_answer(text, result, question) + f"\n\n(The AI had a problem: {exc})", "engine": "offline", "error": str(exc)}


def _offline_answer(text: str, result: dict, question: str) -> str:
    """Keyword search: show the sentences that share the most words with the question."""
    import re

    stop = {"the", "a", "an", "is", "are", "what", "when", "how", "do", "i", "my", "this", "does", "of", "to", "in", "it", "and", "or", "for", "can", "me", "on", "be", "will"}
    words = {w for w in re.findall(r"[a-z0-9']+", question.lower()) if w not in stop and len(w) > 2}
    if not words:
        return "Try asking with a few more specific words, like 'deposit' or 'late fee'."
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if len(s.strip()) > 15]
    scored = sorted(((sum(w in s.lower() for w in words), s) for s in sentences), key=lambda x: -x[0])
    hits = [s for score, s in scored[:3] if score > 0]
    if not hits:
        return "I couldn't find those words in the document. Basic mode can only search the text; pick an AI option in Settings for real answers."
    return "Basic mode can't reason, but here are the parts of the document that match your question:\n\n" + "\n\n".join(f"“{h[:400]}”" for h in hits)
