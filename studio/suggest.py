"""Offer, hook, and title options. An LLM replaces the template when a key is set."""

from __future__ import annotations

from studio.llm import complete_json, suggestion_mode

SYSTEM = """You are the pre-production desk for a B2B YouTube video.
The spoken hook is either an 8-second opener (proof or pain first, no autobiography) or a who/what/why under 40 seconds.
Titles are short enough to say out loud. Thumbnail lines are 2 or 3 words, uppercase, glanced not read.
Return only JSON with this shape:
{"offers":[{"id":"a","name":"","who":"","pitch":""}],"hooks":[{"id":"a","label":"","seconds":"8s","text":""}],"titles":[{"id":"a","text":"","thumb_lines":["WORD","WORD"],"highlight":"WORD"}]}
Exactly 3 offers, 5 hooks, 3 titles.
One hook label must be "Who / what / why". One hook label must be "8-second proof".
highlight must be one of the thumb_lines.
Do not mention agencies, editors, or these instructions.
"""


def _clean(value: str) -> str:
    return " ".join((value or "").split())


def suggest(brief: dict) -> tuple[dict, str, str | None]:
    """Return suggestions, source name, and an optional warning."""
    if suggestion_mode() == "template":
        return template_suggestions(brief), "template", None
    try:
        raw = complete_json(SYSTEM, _brief_block(brief))
        cleaned = _normalize(raw, brief)
        return cleaned, suggestion_mode(), None
    except Exception as exc:  # fall back so the desk still works
        warning = f"Model call failed ({exc}). Showing template options from your brief."
        return template_suggestions(brief), "template", warning


def script_from_picks(brief: dict, picks: dict, suggestions: dict) -> tuple[str, str, str | None]:
    offer = _by_id(suggestions["offers"], picks["offer_id"])
    hook = _by_id(suggestions["hooks"], picks["hook_id"])
    title = _by_id(suggestions["titles"], picks["title_id"])
    if suggestion_mode() == "template":
        return template_script(brief, offer, hook, title), "template", None
    system = (
        "Write a B2B YouTube script. Keep the chosen hook as the first spoken lines, verbatim. "
        "Use these picture markers on their own lines: [[face]], [[screen: what to show]], [[card: THREE WORDS]]. "
        "Put a short verbal CTA around a third of the way through and a direct CTA at the end. "
        "A picture must change at least every 30 seconds of talking. "
        "Return only JSON: {\"script\": \"...\"}."
    )
    user = (
        _brief_block(brief)
        + "\n\nCHOSEN OFFER\n"
        + offer["pitch"]
        + "\n\nCHOSEN HOOK\n"
        + hook["text"]
        + "\n\nCHOSEN TITLE\n"
        + title["text"]
    )
    try:
        raw = complete_json(system, user)
        text = (raw.get("script") or "").strip()
        if hook["text"].split(".")[0][:40] not in text:
            text = hook["text"].strip() + "\n\n" + text
        return text, suggestion_mode(), None
    except Exception as exc:
        warning = f"Model call failed ({exc}). Script was built from the options you picked."
        return template_script(brief, offer, hook, title), "template", warning


def template_suggestions(brief: dict) -> dict:
    icp = _clean(brief.get("icp") or "the buyer")
    pain = _clean(brief.get("pain") or "the usual channel stopped working")
    topic = _clean(brief.get("topic") or "the system")
    offer = _clean(brief.get("current_offer") or "the service")
    proof = _clean(brief.get("proof") or "the result you can stand behind")
    company = _clean(brief.get("company") or "the company")

    offers = [
        {
            "id": "dfy",
            "name": f"Done-for-you {offer}",
            "who": icp,
            "pitch": (
                f"{company} runs {offer} for {icp}. "
                "They show up for the sales calls. The production and the system stay off their plate."
            ),
        },
        {
            "id": "audit",
            "name": "Paid diagnostic",
            "who": icp,
            "pitch": (
                f"A fixed-scope audit for {icp} who feel {pain}. "
                f"They leave with the one change in {topic} that is worth making before they buy the full offer."
            ),
        },
        {
            "id": "sprint",
            "name": "30-day sprint",
            "who": icp,
            "pitch": (
                f"One outcome in 30 days, sliced out of {offer}. "
                f"Built for {icp} who want proof on their own account before a retainer."
            ),
        },
    ]
    hooks = [
        {
            "id": "who",
            "label": "Who / what / why",
            "seconds": "20s",
            "text": (
                f"If you're {icp} and {pain}, this is for you. "
                f"I'm going to walk through {topic}, step by step. "
                f"{proof}."
            ),
        },
        {
            "id": "eight",
            "label": "8-second proof",
            "seconds": "8s",
            "text": f"{proof}. This is the {topic} behind that, for {icp}.",
        },
        {
            "id": "pain",
            "label": "Pain first",
            "seconds": "12s",
            "text": (
                f"{pain.capitalize() if pain[:1].islower() else pain}. "
                f"If that is you, and you sell to {icp}, stay. "
                f"The next few minutes are the {topic} we use, and {proof}."
            ),
        },
        {
            "id": "plan",
            "label": "Proof, promise, plan",
            "seconds": "18s",
            "text": (
                f"{proof}. "
                f"By the end of this you'll have the {topic} you can run this month. "
                f"Three parts: who it's for, the steps, and where {offer} fits if you don't want to do it yourself."
            ),
        },
        {
            "id": "search",
            "label": "Search intent",
            "seconds": "15s",
            "text": (
                f"You searched for {topic}. "
                f"Here is the version we run for {icp}, including the part most videos skip. "
                f"{proof}."
            ),
        },
    ]
    search_lines = _thumb_from(topic)
    titles = [
        {
            "id": "search-title",
            "text": f"{topic} for {icp}",
            "thumb_lines": search_lines,
            "highlight": search_lines[-1].split()[-1],
        },
        {
            "id": "proof-title",
            "text": f"{_short(proof)}. Here's the system.",
            "thumb_lines": ["THE SYSTEM", "BEHIND IT"],
            "highlight": "SYSTEM",
        },
        {
            "id": "pain-title",
            "text": f"Stop losing {icp} to {pain.split(',')[0]}",
            "thumb_lines": ["STOP", "LOSING THEM"],
            "highlight": "STOP",
        },
    ]
    return {"offers": offers, "hooks": hooks, "titles": titles}


def template_script(brief: dict, offer: dict, hook: dict, title: dict) -> str:
    topic = _clean(brief.get("topic") or "the system")
    proof = _clean(brief.get("proof") or "the result")
    cta = _clean(brief.get("cta") or "book a call")
    card = _clean(brief.get("proof_card") or _card_from_proof(proof))
    return "\n".join(
        [
            "[[face]]",
            hook["text"].strip(),
            "",
            "[[card: " + card + "]]",
            f"That is the whole promise of this video: {title['text']}",
            "",
            "[[screen: the actual tool, full frame]]",
            f"Let me show you {topic} inside the tool, not on a slide.",
            "I'll mark the one control that matters and ignore the rest of the dashboard.",
            "",
            "[[face]]",
            f"If you want this done for you, {offer['name']} is the offer. {offer['pitch']} "
            f"The link to {cta} is in the description. Then I'll keep going.",
            "",
            "[[screen: the second step in the same tool]]",
            "Second step. Same screen, zoomed into the setting people skip.",
            "",
            "[[broll: hands, notebook, or the object you mentioned]]",
            "Write this part down. It is the piece that makes the rest of the system hold.",
            "",
            "[[face]]",
            f"So. {proof}.",
            "",
            "[[card: " + offer["name"][:28].upper() + "]]",
            f"If you are the person this was for, {cta}. "
            "Tell us where you are starting from, and we will tell you if it is a fit.",
        ]
    )


def _card_from_proof(proof: str) -> str:
    words = proof.replace(",", " ").split()
    for word in words:
        if any(char.isdigit() for char in word):
            return word[:18].upper()
    return "THE NUMBER"


def _thumb_from(topic: str) -> list[str]:
    words = [word.strip(".,").upper() for word in topic.split() if word.strip(".,")]
    if len(words) >= 3:
        return [" ".join(words[:2])[:16], words[2][:16]]
    if len(words) == 2:
        return [words[0][:16], words[1][:16]]
    return [(words[0] if words else "THE VIDEO")[:16], "SYSTEM"]


def _short(proof: str) -> str:
    sentence = proof.split(".")[0].strip()
    if len(sentence) > 70:
        return sentence[:67].rstrip() + "..."
    return sentence


def _brief_block(brief: dict) -> str:
    lines = [f"{key}: {brief.get(key) or ''}" for key in (
        "founder_name", "company", "icp", "pain", "topic", "current_offer", "proof", "cta",
    )]
    return "\n".join(lines)


def _by_id(items: list[dict], item_id: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise KeyError(item_id)


def _normalize(raw: dict, brief: dict) -> dict:
    fallback = template_suggestions(brief)
    offers = _items(raw.get("offers"), fallback["offers"], ("name", "who", "pitch"))
    hooks = _items(raw.get("hooks"), fallback["hooks"], ("label", "seconds", "text"))
    titles = []
    incoming = raw.get("titles") if isinstance(raw.get("titles"), list) else []
    for index, item in enumerate(incoming[:3]):
        if not isinstance(item, dict):
            continue
        lines = item.get("thumb_lines") or []
        lines = [str(line).strip().upper() for line in lines if str(line).strip()][:3]
        if len(lines) < 2:
            continue
        highlight = str(item.get("highlight") or lines[0]).strip().upper()
        if highlight not in lines and not any(highlight in line for line in lines):
            highlight = lines[0].split()[0]
        titles.append(
            {
                "id": f"t{index}",
                "text": _clean(str(item.get("text") or "Untitled")),
                "thumb_lines": lines,
                "highlight": highlight,
            }
        )
    if len(titles) < 3:
        titles = fallback["titles"]
    return {"offers": offers, "hooks": hooks, "titles": titles}


def _items(incoming, fallback: list[dict], fields: tuple[str, ...]) -> list[dict]:
    cleaned = []
    if isinstance(incoming, list):
        for index, item in enumerate(incoming):
            if not isinstance(item, dict):
                continue
            row = {"id": f"i{index}"}
            ok = True
            for field in fields:
                value = _clean(str(item.get(field) or ""))
                if not value:
                    ok = False
                row[field] = value
            if ok:
                cleaned.append(row)
    needed = 5 if "text" in fields and "seconds" in fields else 3
    if len(cleaned) < needed:
        return fallback
    return cleaned[:needed]
