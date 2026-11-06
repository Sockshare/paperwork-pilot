"""Offline rules engine.

No AI at all. Pure Python pattern matching that still gives a useful first
read of a document: what kind it is, dates, dollar amounts, and sentences
that commonly hide gotchas (auto-renewal, late fees, arbitration, ...).

It is used in three situations:
  1. The user picked "Offline mode".
  2. The AI provider failed (we show the rules result + a gentle note).
  3. As a safety net merged UNDER the AI result, so a date or dollar amount
     the model missed still shows up.
"""

from __future__ import annotations

import re
from datetime import datetime

from .schema import DOC_TYPES, normalize

# ---------------------------------------------------------------------------
# Document type detection
# ---------------------------------------------------------------------------
TYPE_KEYWORDS: dict[str, list[str]] = {
    "lease": ["lease", "tenant", "landlord", "security deposit", "premises", "rent is due", "lessee", "lessor", "sublet"],
    "medical_bill": ["patient", "date of service", "cpt", "copay", "co-pay", "deductible", "amount you owe", "provider", "hospital", "clinic", "statement balance", "insurance paid"],
    "insurance": ["explanation of benefits", "eob", "claim number", "policy number", "this is not a bill", "denied", "denial", "appeal", "coverage", "insured", "premium"],
    "job_offer": ["offer of employment", "pleased to offer", "start date", "base salary", "annual salary", "at-will", "benefits package", "reporting to", "equity", "stock options", "sign-on bonus"],
    "terms": ["terms of service", "terms and conditions", "end user", "you agree", "subscription", "auto-renew", "automatically renew", "license", "arbitration", "class action"],
    "debt": ["debt collector", "collection agency", "amount of the debt", "original creditor", "validate", "dispute the debt", "fair debt collection", "past due balance", "settle"],
    "utility": ["kwh", "meter", "usage", "service address", "account number", "billing period", "due date", "electric", "gas service", "water service", "wireless", "data plan"],
    "government": ["internal revenue service", "irs", "department of", "social security", "notice number", "tax year", "dmv", "benefits determination", "cp2000", "form 1040"],
    "legal": ["summons", "complaint", "court", "plaintiff", "defendant", "hereby notified", "cease and desist", "subpoena", "notice to quit", "eviction"],
}

SENDER_PATTERNS = [
    r"^from:\s*(.+)$",
    r"operated by ([A-Z][A-Za-z&.,' ]{2,60}?(?:Inc\.?|LLC|Corp\.?|Company|Co\.|Group|Ltd\.?))",
    r"^(?:sincerely|regards|best regards|thank you),?\s*\n+\s*(.{3,80})$",
    r"^([A-Z][A-Za-z&.,' ]{2,60}(?:Inc\.?|LLC|Corp\.?|Company|Co\.|Bank|Hospital|Clinic|Properties|Management|Services|Insurance|Collections?|Department|Agency|University|Group))\b",
]

# ---------------------------------------------------------------------------
# Watch-out patterns: (title, regex, severity, why)
# ---------------------------------------------------------------------------
WATCH_PATTERNS: list[tuple[str, str, str, str]] = [
    ("Automatic renewal", r"auto(?:matic(?:ally)?)?[- ]?renew", "high",
     "It keeps going and keeps charging you unless you cancel in writing, usually before a cut-off date."),
    ("Late fee", r"late (?:fee|charge|payment penalty)|penalt(?:y|ies) for late", "high",
     "Paying even one day late can cost extra money."),
    ("Early termination / cancellation fee", r"early termination|termination fee|cancellation fee|break(?:ing)? (?:the )?lease", "high",
     "Leaving early could cost you a large amount."),
    ("You give up your right to sue (arbitration)", r"arbitrat|waive[sd]? (?:your|the) right to (?:a )?(?:jury|trial|sue)|class[- ]action waiver", "high",
     "Disputes go to a private arbitrator instead of court, and you usually can't join a class action."),
    ("Non-refundable", r"non[- ]?refundable", "medium",
     "If plans change, you will not get this money back."),
    ("Interest or finance charges", r"\b(?:apr|interest rate|finance charge|interest will accrue|accrue[sd]? interest)\b", "medium",
     "The amount you owe grows over time if you don't pay it off."),
    ("Price can go up", r"(?:rate|price|rent|premium|fee)s? (?:may|can|will) (?:be )?(?:increase|change|adjust)|subject to change", "medium",
     "What you pay today is not locked in."),
    ("Personal guarantee / co-signer", r"personal(?:ly)? guarant|co-?signer|guarantor (?:shall|will|agrees|must|is responsible)", "medium",
     "Someone is personally on the hook for the money, even beyond a business or deposit."),
    ("They can enter your home", r"right (?:of|to) entry|enter the premises", "low",
     "Check how much notice they must give before coming in."),
    ("Deadline to respond or dispute", r"(?:respond|dispute|appeal|reply|notify|notice|object|contest|request|sign and return)[^.]{0,80}within (?:\d+|thirty|sixty|ninety|ten|fifteen|one hundred eighty) (?:\(\d+\) )?(?:calendar |business )?days|within (?:\d+|thirty|sixty|ninety|ten|fifteen|one hundred eighty) (?:\(\d+\) )?(?:calendar |business )?days[^.]{0,80}(?:respond|dispute|appeal|reply|notify|object|contest|of the date of this letter)", "high",
     "If you miss this window you may lose the right to dispute or appeal."),
    ("Balance sent to collections", r"(?:sent|referred|turned over) to (?:a )?collection|collection agency", "high",
     "This can hurt your credit score. Act before it happens."),
    ("Non-compete or non-solicit", r"non[- ]?compete|non[- ]?solicit", "medium",
     "It may limit where you can work after you leave."),
    ("At-will employment", r"at[- ]will", "low",
     "Either side can end the job at any time, for almost any reason."),
    ("Probation / trial period", r"probation(?:ary)? period|trial period|introductory period", "low",
     "Benefits or protections may not start until this period ends."),
    ("Your data may be shared or sold", r"(?:share|sell|disclose)[sd]? (?:your )?(?:personal )?(?:data|information) (?:with|to) (?:third|our) part", "medium",
     "Your personal information may go to other companies."),
    ("Automatic payments / autopay", r"auto-?pay|automatic(?:ally)? (?:charge|debit|withdraw)|recurring (?:payment|charge|billing)", "medium",
     "Money will be taken from your account without asking each time."),
    ("Pre-authorization / prior approval needed", r"pre-?authoriz|prior (?:authorization|approval)", "medium",
     "Without approval first, insurance may refuse to pay."),
    ("Out of network", r"out[- ]of[- ]network", "medium",
     "Out-of-network care usually costs you much more."),
]

# One keyword per watch-out topic, used to avoid listing the same gotcha twice
# when the AI already mentioned it in its own words.
WATCH_TOPIC = {
    "Automatic renewal": "renew",
    "Late fee": "late",
    "Early termination / cancellation fee": "terminat",
    "You give up your right to sue (arbitration)": "arbitrat",
    "Non-refundable": "refund",
    "Interest or finance charges": "interest",
    "Price can go up": "increase",
    "Personal guarantee / co-signer": "guarant",
    "They can enter your home": "enter",
    "Deadline to respond or dispute": "within",
    "Balance sent to collections": "collect",
    "Non-compete or non-solicit": "solicit",
    "At-will employment": "at-will",
    "Probation / trial period": "introductory",
    "Your data may be shared or sold": "share",
    "Automatic payments / autopay": "autopay",
    "Pre-authorization / prior approval needed": "authoriz",
    "Out of network": "network",
}

MONEY_RE = re.compile(r"(?<![\w.])\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\b\d{1,3}(?:,\d{3})*(?:\.\d{2})?\s?(?:USD|dollars)\b", re.I)
DATE_RE = re.compile(
    r"\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4})\b",
    re.I,
)
DEADLINE_WORDS = re.compile(r"\b(due|deadline|by|before|no later than|expires?|must|within|respond|pay|renew|terminate|notice|end|start|begins?|effective)\b", re.I)
RECURRING_WORDS = re.compile(r"\b(per month|monthly|each month|a month|/mo|per year|annually|yearly|per week|weekly|every)\b", re.I)

GLOSSARY = {
    "security deposit": "Money you pay up front that the landlord holds. You should get it back if you leave the place in good shape.",
    "deductible": "The amount you pay yourself each year before insurance starts paying.",
    "copay": "A fixed amount you pay for a visit or prescription.",
    "coinsurance": "Your share of a bill as a percentage, after the deductible.",
    "out-of-pocket maximum": "The most you will have to pay in a year. After this, insurance pays 100%.",
    "explanation of benefits": "A letter from insurance showing what they paid. It is not a bill.",
    "arbitration": "A private process to settle disputes instead of going to court.",
    "at-will": "The job can end at any time, by you or the employer, for almost any reason.",
    "non-compete": "A promise not to work for competitors for a while after leaving.",
    "pro rata": "Split by the share of time used. Half a month means half the price.",
    "liquidated damages": "A set penalty amount written into the contract.",
    "indemnify": "Promise to cover someone else's losses or legal costs.",
    "garnishment": "Money taken straight from your paycheck or bank to pay a debt.",
    "validation": "Your right to make a debt collector prove the debt is real and yours.",
    "original creditor": "The company you first owed the money to, before it was handed to a collector.",
    "settlement": "Paying less than the full amount, and the rest is forgiven. Get it in writing first.",
    "dispute": "Telling them in writing that you disagree, and why. Always keep a copy.",
    "statute of limitations": "The time limit for someone to sue over a debt or claim.",
    "prior authorization": "Insurance must say yes BEFORE you get a treatment, or they may not pay.",
    "in-network": "Doctors and hospitals that have a deal with your insurance. Cheaper for you.",
    "out-of-network": "Providers without a deal with your insurance. Usually much more expensive.",
    "vesting": "Earning ownership of stock or retirement money over time.",
    "sublet": "Renting your place to someone else while your name is still on the lease.",
    "escalation clause": "A rule that lets the price go up over time.",
    "apr": "The yearly interest rate, including fees.",
}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def detect_type(text: str) -> str:
    low = text.lower()
    scores = {t: 0 for t in TYPE_KEYWORDS}
    for t, kws in TYPE_KEYWORDS.items():
        for kw in kws:
            scores[t] += low.count(kw) * (2 if len(kw) > 8 else 1)
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "other"


def find_sender(text: str) -> str:
    head = text[:1500]
    # Letterhead: an ALL-CAPS first line like "RIVERBEND REGIONAL MEDICAL CENTER"
    first = next((ln.strip() for ln in head.split("\n") if ln.strip()), "")
    if 4 <= len(first) <= 70 and first.upper() == first and re.search(r"[A-Z]{3}", first) and not re.search(r"\b(AGREEMENT|LEASE|NOTICE|TERMS|STATEMENT|INVOICE|BILL|OFFER|LETTER)\b", first):
        name = first.title().replace("Llc", "LLC").replace("'S", "'s")
        return re.sub(r"\b(Ppo|Hmo|Eob|Irs|Dmv|Usa|Llp|Pc|Md|Ltd)\b", lambda m: m.group(1).upper(), name)
    for pat in SENDER_PATTERNS:
        m = re.search(pat, head, re.I | re.M)
        if m:
            return m.group(1).strip()[:80]
    return ""


def _parse_date(s: str) -> datetime | None:
    s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s.strip().rstrip(","))
    s = re.sub(r"\bSept\b\.?", "Sep", s, flags=re.I)
    s = re.sub(r"\b([A-Za-z]{3})\.", r"\1", s)  # "Nov." -> "Nov"
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _urgency(d: datetime | None) -> str:
    if not d:
        return "soon"
    days = (d - datetime.now()).days
    if days < 0:
        return "none"
    if days <= 14:
        return "urgent"
    if days <= 60:
        return "soon"
    return "later"


BASIC_NOTE = "This is a basic scan without AI. Pick an AI option in Settings for a full plain-language explanation."


def analyze_rules(text: str) -> dict:
    dtype = detect_type(text)
    sentences = _sentences(text)
    sender = find_sender(text)

    # Deadlines
    deadlines, seen_dates = [], set()
    for sent in sentences:
        for m in DATE_RE.finditer(sent):
            raw_date = m.group(0)
            if raw_date.lower() in seen_dates:
                continue
            if not DEADLINE_WORDS.search(sent):
                continue
            seen_dates.add(raw_date.lower())
            deadlines.append({"date": raw_date, "what": sent[:220], "urgency": _urgency(_parse_date(raw_date))})
    # soonest first when we can parse
    deadlines.sort(key=lambda d: (_parse_date(d["date"]) or datetime.max))

    # Money — use the LINE an amount sits on (bills are line-by-line), falling back to the sentence
    money, seen_amounts = [], set()
    for line in text.split("\n"):
        line = re.sub(r"\s{2,}", " — ", line.strip())  # "CT Scan        $2,310.00" -> "CT Scan — $2,310.00"
        if not line:
            continue
        for m in MONEY_RE.finditer(line):
            amt = m.group(0).replace(" ", "")
            key = (amt, line[:60])
            if key in seen_amounts:
                continue
            seen_amounts.add(key)
            what = line if len(line) <= 220 else line[:217] + "..."
            money.append({"amount": amt, "what": what, "recurring": bool(RECURRING_WORDS.search(line))})
    money = money[:20]

    # Key facts — reference numbers, main amount, main date
    facts = []
    for label, pat in (
        ("Account number", r"account (?:number|no\.?|#)[:\s]+([A-Z0-9-]{4,})"),
        ("Claim number", r"claim (?:number|no\.?|#)[:\s]+([A-Z0-9-]{4,})"),
        ("Reference number", r"(?:reference|ref\.?|member id|policy number)[:\s#]+([A-Z0-9-]{4,})"),
        ("Amount due", r"(?:amount (?:you owe|due)|total (?:amount )?due|balance due|current balance)[:\s]+(\$\s?[\d,]+(?:\.\d{2})?)"),
        ("Due date", r"(?:payment )?due (?:date|by|on)[:\s]+([A-Za-z]+\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})"),
        ("Start date", r"(?:start date|begins? on|effective)[:\s]+(?:is )?(?:[A-Za-z]+, )?([A-Za-z]+\.? \d{1,2},? \d{4})"),
        ("Salary", r"(?:base )?salary (?:will be|of|is)[:\s]+(\$[\d,]+(?:\.\d{2})?(?: per year| annually)?)"),
        ("Monthly rent", r"rent of (\$[\d,]+(?:\.\d{2})?) per month"),
    ):
        m = re.search(pat, text, re.I)
        if m:
            facts.append({"label": label, "value": m.group(1).strip()})
    if sender:
        facts.insert(0, {"label": "From", "value": sender})

    # Watch-outs
    watch = []
    for title, pattern, severity, why in WATCH_PATTERNS:
        rx = re.compile(pattern, re.I)
        hits = [sent for sent in sentences if rx.search(sent)]
        if hits:
            # Prefer a full sentence over a short heading like "EARLY TERMINATION."
            best = next((h for h in hits if len(h) >= 40), hits[0])
            watch.append({"title": title, "why": why, "quote": best[:300], "severity": severity})

    # Glossary — only terms actually present
    low = text.lower()
    glossary = [{"term": t, "meaning": m} for t, m in GLOSSARY.items() if t in low][:12]

    # Summary (template)
    label = DOC_TYPES[dtype]
    article = "an" if label[0].lower() in "aeiou" else "a"
    parts = [f"This looks like {article} {label.lower()}."]
    if sender:
        parts.append(f"It appears to come from {sender}.")
    if deadlines:
        if len(deadlines) == 1:
            parts.append(f"I found one date that may be a deadline: {deadlines[0]['date']}.")
        else:
            parts.append(f"I found {len(deadlines)} dates that may be deadlines; the first is {deadlines[0]['date']}.")
    if money:
        parts.append(f"There are {len(money)} dollar amount{'s' if len(money) != 1 else ''} mentioned, including {money[0]['amount']}.")
    if watch:
        highs = [w["title"] for w in watch if w["severity"] == "high"]
        if highs:
            parts.append("Pay special attention to: " + ", ".join(highs[:3]) + ".")
    parts.append(BASIC_NOTE)

    next_steps = ["Read the 'Watch out for' items first.", "Write the dates on your calendar.", "Keep a copy of this document."]
    options = {
        "lease": ["You can ask the landlord to change terms before you sign.", "You can request repairs in writing.", "Your city may have tenant rights rules; search for '<your city> tenant rights'."],
        "medical_bill": ["Ask for an itemized bill — mistakes are common.", "Ask about a payment plan or financial assistance.", "Compare the bill with your insurance Explanation of Benefits."],
        "insurance": ["You usually have the right to appeal a denial in writing.", "Ask for the exact policy rule they used to decide.", "Your doctor's office can often help with appeals."],
        "job_offer": ["You can negotiate salary, start date, vacation and remote work.", "Ask for anything verbal to be put in writing.", "Ask about health insurance start date and retirement match."],
        "debt": ["You can ask the collector to validate the debt in writing within 30 days.", "You can ask them to only contact you by mail.", "Check whether the debt is past your state's time limit."],
        "terms": ["Look for how to cancel and when the price changes.", "Check whether a free trial turns into a paid plan."],
        "utility": ["Ask about budget billing to even out monthly payments.", "Ask whether there are assistance programs if money is tight."],
        "government": ["Respond before the deadline even if you disagree.", "Keep copies of everything you send.", "Free help may be available (for taxes: VITA or Taxpayer Advocate)."],
        "legal": ["Do not ignore it — missing a court date can mean losing automatically.", "Free or low-cost legal aid may be available in your area."],
    }.get(dtype, ["Ask the sender to explain anything unclear, in writing."])
    questions = {
        "lease": ["What exactly do I get my deposit back for?", "How much notice do you give before entering?", "What happens if I need to leave early?"],
        "medical_bill": ["Can you send an itemized bill?", "Was this sent to my insurance?", "Do you offer a payment plan or discount?"],
        "insurance": ["What rule was used to deny this?", "How do I file an appeal and by when?", "What paperwork do you need from my doctor?"],
        "job_offer": ["Is the salary negotiable?", "When do benefits start?", "Is there a bonus or stock plan?"],
        "debt": ["Who is the original creditor?", "Can you send proof I owe this?", "What is the date of the last payment?"],
    }.get(dtype, ["Can you explain this in plain words?", "What happens if I do nothing?", "Is there a deadline?"])

    return normalize(
        {
            "document_type": dtype,
            "title": label,
            "plain_summary": " ".join(parts),
            "who_sent_it": sender,
            "what_they_want": "",
            "key_facts": facts,
            "deadlines": deadlines,
            "money": money,
            "watch_outs": watch,
            "your_options": options,
            "questions_to_ask": questions,
            "next_steps": next_steps,
            "glossary": glossary,
            "confidence": "low",
            "notes": ["Basic scan (no AI). Dates and amounts were found by pattern matching, so check them against the original."],
        }
    )


def merge_safety_net(ai: dict, rules: dict) -> dict:
    """Add rules-found dates/amounts/watch-outs that the AI missed."""
    out = dict(ai)

    def _has(items, key, value):
        v = value.lower()
        return any(v and v in str(i.get(key, "")).lower() for i in items)

    for d in rules["deadlines"]:
        if d["date"] and not _has(out["deadlines"], "date", d["date"]) and not _has(out["deadlines"], "what", d["date"]):
            out["deadlines"].append(d)
    for m in rules["money"]:
        if m["amount"] and not _has(out["money"], "amount", m["amount"]) and not _has(out["money"], "what", m["amount"]):
            out["money"].append(m)
    ai_blob = " ".join((x.get("title", "") + " " + x.get("why", "") + " " + x.get("quote", "")).lower() for x in out["watch_outs"])
    for w in rules["watch_outs"]:
        topic = WATCH_TOPIC.get(w["title"], w["title"].split(" ")[0].lower())
        already = topic in ai_blob or (w["quote"] and w["quote"][:40].lower() in ai_blob)
        if not already:
            out["watch_outs"].append(w)
            ai_blob += " " + topic
    have_terms = {g["term"].lower() for g in out["glossary"]}
    for g in rules["glossary"]:
        if g["term"].lower() not in have_terms:
            out["glossary"].append(g)
    out["deadlines"] = out["deadlines"][:15]
    out["money"] = out["money"][:20]
    order = {"high": 0, "medium": 1, "low": 2}
    out["watch_outs"] = sorted(out["watch_outs"], key=lambda w: order.get(w.get("severity", "medium"), 1))[:15]
    out["glossary"] = out["glossary"][:15]
    return out
