from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from jinja2 import DictLoader, Environment, select_autoescape
from markupsafe import Markup, escape

from tripwright.checks import departure_clock
from tripwright.service import Tripwright
from tripwright.storage import day_document
from tripwright.render.memory import counts as memory_counts, rows as memory_rows, summary_line
from tripwright.render.pack import material_lines

from .assets import SCRIPT, STYLE
from .privacy import Policy, PrivacyError, publication_text, redact_values
from .templates import BASE, DAY, DECISIONS, INDEX, MACROS, MEMORY, RESULT


def _label(chinese: str, english: str) -> Markup:
    return Markup('<span class="zh">{}</span><span class="en">{}</span>').format(escape(chinese), escape(english))


def _missing(text: str = "No item-specific verification recorded.") -> dict:
    return dict(status="unverified", text=text,
                consequence="This part of the plan may be infeasible.",
                alternative="Confirm before acting and retain a simpler fallback.")


def _item_checks(checks: list[dict], identifier: str) -> list[dict]:
    related = [check for check in checks if identifier in check.get("id", "").split(":")]
    if not related or not any(check.get("id", "").startswith("evidence:") for check in related):
        related = [*related, _missing()]
    return related


def _place(place: dict) -> dict:
    view = deepcopy(place)
    private = str(place.get("kind", "")).casefold() in {"住宿", "lodging", "hotel", "accommodation"}
    if private:
        view.update(name="Private stay", name_local="Private stay", coords=None, hours=None, reason=None)
    view["map_url"] = ("https://www.google.com/maps/search/?api=1&query=" + quote(place["name_local"])) if not private else None
    return view


def _day(version: dict, raw: dict) -> dict:
    day = deepcopy(raw)
    checks = day_document(version, raw)["verification"]["checks"]
    places = {place["id"]: _place(place) for place in version["plan"].get("places", [])}
    references = []
    for anchor in day.get("anchors", []):
        anchor["latest_departure"] = departure_clock(anchor["time"], anchor["buffer_minutes"], anchor.get("travel_minutes"),
                                                    day=day["date"], zone=version["plan"]["timezone"])
        anchor["verification"] = _item_checks(checks, anchor["id"])
        if anchor.get("place"):
            references.append(anchor["place"])
    for plan in day.get("plans", []):
        for track in plan["tracks"]:
            for block in track["blocks"]:
                place = places.get(block.get("place"))
                block["place_view"] = place
                block["title"] = place["name_local"] if place else block.get("text") or block["kind"]
                block["verification"] = _item_checks(checks, block["id"])
                block["option_views"] = [places[identifier] for identifier in block.get("options", []) if identifier in places]
                references.extend([block.get("place"), *block.get("options", [])])
                if place:
                    place.setdefault("verification", []).extend(block["verification"])
    day["candidate_views"] = {slot: [places[identifier] for identifier in identifiers if identifier in places]
                              for slot, identifiers in day.get("candidates", {}).items()}
    for identifiers in day.get("candidates", {}).values():
        references.extend(identifiers)
    for place in places.values():
        place.setdefault("verification", [_missing("No place-specific opening or access verification recorded.")])
    day["places"] = [places[identifier] for identifier in dict.fromkeys(references) if identifier in places]
    day["verification"] = checks
    day["fares"] = []
    day["totals"] = {}
    for booking in version["plan"].get("bookings", []):
        if booking.get("status") == "cancelled":
            continue
        for source in booking.get("fares", []):
            if (source.get("date") or booking["date"]) != day["date"]:
                continue
            fare = dict(source, verification=_item_checks(checks, source["id"]))
            day["fares"].append(fare)
            if fare.get("included_in") or fare.get("alternative") or fare.get("amount_per_person") is None or not fare.get("currency"):
                continue
            for person in fare.get("subject", []):
                totals = day["totals"].setdefault(person, {})
                currency = fare["currency"]
                totals[currency] = totals.get(currency, Decimal(0)) + Decimal(str(fare["amount_per_person"]))
    return day


def _context(service: Tripwright, owner: str, *, language: str | None = None, now: datetime | None = None) -> dict:
    version = service.state(owner)
    if not version.get("plan"):
        raise ValueError("No committed trip")
    trip = version["plan"]
    profiles = service.store.all("profiles", owner)
    inferred = "zh" if any("\u3400" <= character <= "\u9fff" for character in trip["title"]) else "en"
    preferred = next((profile.get("language") for profile in profiles if profile.get("language")), inferred)
    language = language or trip.get("language") or preferred
    language = "zh" if language.lower().startswith("zh") else "en"
    zone = ZoneInfo(trip["timezone"])
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("now must include a timezone")
    clock = clock.astimezone(zone)
    days = [_day(version, day) for day in sorted(trip.get("days", []), key=lambda day: day["date"])]
    by_date = {day["date"]: day for day in days}
    names = {person["id"]: person["name"] for person in trip.get("people", [])}

    def people(subject: list[str]) -> str:
        return ", ".join(names.get(identifier, identifier) for identifier in subject)

    def status_label(status: str) -> Markup:
        if status == "passed":
            return _label("已核（本项）", "Verified (this check)")
        if status == "conflict":
            return _label("冲突", "Conflict")
        return _label("未核", "Unverified")

    checks = version.get("checks") or {}
    decisions = deepcopy(service.store.all("decisions", owner))
    for decision in decisions:
        source = decision.get("source") or {}
        if isinstance(source, dict):
            decision["source"] = " · ".join(str(source[key]) for key in ("nature", "basis") if source.get(key)) or "Not recorded"
    return dict(version=version, trip=trip, days=days, language=language, generated=clock.isoformat(timespec="seconds"),
                checked_at=checks.get("checked_at"), label=_label, people=people, status_label=status_label,
                style=STYLE, script=SCRIPT, result_page=False,
                nearby=[(_label("今天", "Today"), by_date.get(clock.date().isoformat())),
                        (_label("明天", "Tomorrow"), by_date.get((clock.date() + timedelta(days=1)).isoformat()))],
                today=by_date.get(clock.date().isoformat()),
                upcoming=next((day for day in days if day["date"] > clock.date().isoformat()), None),
                actions=[booking for booking in trip.get("bookings", []) if booking["status"] in ("to_buy", "to_book", "to_contact")],
                decisions=decisions, undecided=service.store.all("undecided", owner),
                pending=service.store.all("pending_checks", owner),
                attention=[check for check in checks.get("checks", []) if check["status"] != "passed"])


def _render(template: str, context: dict, **values) -> str:
    environment = Environment(loader=DictLoader(dict(base=BASE, macros=MACROS, index=INDEX, day=DAY,
                                                     decisions=DECISIONS, result=RESULT, memory=MEMORY)),
                              autoescape=select_autoescape(default=True))
    return environment.get_template(template).render(**dict(context, **values))


def _policy(trip: dict, additional: Policy | None) -> Policy:
    lines = [f"{person['name']} => Traveler {index}" for index, person in enumerate(trip.get("people", []), 1)]
    for place in trip.get("places", []):
        if str(place.get("kind", "")).casefold() in {"住宿", "lodging", "hotel", "accommodation"}:
            lines.extend(f"{place[key]} => Private stay" for key in ("name", "name_local") if place.get(key))
    policy = Policy(lines)
    if additional is not None:
        policy.replacements.extend(additional.replacements)
        policy.forbidden.extend(additional.forbidden)
    return policy


LINK = re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")


def _text_html(lines: list[str]) -> Markup:
    """The repository views' Markdown subset as HTML, so the result page shows the same words as the files:
    headings, list items, links and plain lines. The generated-file comment is left out; everything is escaped."""
    html, items = [], []

    def inline(text: str) -> Markup:
        parts, last = [], 0
        for match in LINK.finditer(text):
            parts.append(escape(text[last:match.start()]))
            parts.append(Markup('<a href="{}">{}</a>').format(match.group(2), match.group(1)))
            last = match.end()
        parts.append(escape(text[last:]))
        return Markup("").join(parts)

    def close():
        if items:
            html.append(Markup("<ul>{}</ul>").format(Markup("").join(items)))
            items.clear()

    for line in lines:
        if line.startswith("<!--") or not line.strip():
            close()
        elif line.startswith("- "):
            items.append(Markup("<li>{}</li>").format(inline(line[2:])))
        else:
            close()
            tag = "h3" if line.startswith("# ") else "h4" if line.startswith("## ") else "p"
            html.append(Markup(f"<{tag}>{{}}</{tag}>").format(inline(line.lstrip("#").strip() if tag != "p" else line)))
    close()
    return Markup('<div class="view-text">{}</div>').format(Markup("").join(html))


def _material(context: dict | None) -> dict | None:
    """The first reply's material: the pack the host received for the latest turn (the host's own words are not
    stored). A store rebuilt from the repository keeps no packs; the page says so."""
    if context is None:
        return None
    pack = context.get("pack")
    outline = None
    if pack:
        quick = pack["scenario"] in ("quick", "status")
        outline = dict(zh="回答骨架：" + ("快答" if quick else "第一段"), en="Answer outline: " + ("quick" if quick else "first reply"))
    return dict(created_at=context.get("created_at"), base_version=context.get("base_version"), outline=outline,
                text=_text_html(material_lines(pack)) if pack else None)


def _memory(memory: dict) -> dict:
    """The memory page uses the same rows as `trips/<trip>/views/memory.md`, in both languages for the toggle."""
    sections = [dict(zh=zh, en=en, pairs=list(zip(zh["rows"], en["rows"])))
                for zh, en in zip(memory_rows(memory, "zh"), memory_rows(memory, "en"))]
    return dict(memory_sections=sections, memory_pending=memory_counts(memory)["pending"],
                memory_summary=dict(zh=summary_line(memory, "zh"), en=summary_line(memory, "en")))


def build_site(service_or_store, owner: str, out_dir, *, language: str | None = None,
               privacy_policy: Policy | None = None, now: datetime | None = None, memory: dict | None = None) -> Path:
    """Render an owner's committed version; privacy failures leave the output untouched.

    out_dir must be absent or empty. Additional private words can be denied or
    replaced through Policy. language overrides trip/profile language and title inference.
    memory (the `tripwright.memory/1` data, see `tripwright.render.memory`) adds the memory page.
    """
    service = service_or_store if isinstance(service_or_store, Tripwright) else Tripwright(service_or_store)
    context = _context(service, owner, language=language, now=now)
    context["memory_sections"] = None
    if memory is not None:
        context.update(_memory(memory))
    policy = _policy(context["trip"], privacy_policy)
    context = redact_values(context, policy)
    public_names = {person["id"]: person["name"] for person in context["trip"].get("people", [])}
    context["people"] = lambda subject: ", ".join(public_names.get(identifier, identifier) for identifier in subject)
    pages = {"index.html": _render("index", context, title=context["trip"]["title"]),
             "decisions.html": _render("decisions", context, title="Decisions")}
    if memory is not None:
        pages["memory.html"] = _render("memory", context, title="Memory")
    for index, day in enumerate(context["days"]):
        pages[f"{day['date']}.html"] = _render("day", context, title=day["title"], day=day,
                                            previous=context["days"][index - 1] if index else None,
                                            following=context["days"][index + 1] if index + 1 < len(context["days"]) else None)
    pages = {name: publication_text(text, policy) for name, text in pages.items()}
    output = Path(out_dir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output directory must be absent or empty; existing publications are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    for name, text in pages.items():
        (output / name).write_text(text, encoding="utf-8")
    (output / ".nojekyll").touch()
    return output


def result_html(service: Tripwright, owner: str, *, now: datetime | None = None) -> str:
    context = _context(service, owner, now=now)
    contexts = service.store.all("contexts", owner)
    latest = max(contexts, key=lambda item: (item.get("created_at", ""), item["id"]), default=None)
    first = _material(latest)
    shown = context["today"] or context["upcoming"]
    # The same day view the repository holds and `state(view="day")` returns (block B's `day_view`).
    today_text = _text_html(service.state(owner, view="day", date=shown["date"])["text"].splitlines()) if shown else None
    drafts = {draft["id"]: draft for draft in service.store.all("drafts", owner)
              if latest and draft["ctx_id"] == latest["id"]}
    jobs = [job for job in service.store.all("jobs", owner) if job.get("payload", {}).get("draft_id") in drafts]
    job = max(jobs, key=lambda item: (item.get("created_at", ""), item["id"]), default=None)
    messages = service.store.all("inbox", owner)
    message = max(messages, key=lambda item: (item.get("generated_at", ""), item["id"]), default=None)
    current_message = next((message for message in messages if job and message["job_id"] == job["id"]), None)
    second = None
    if message:
        value = message.get("value") or {}
        report = value.get("checks") or {}
        checks = report.get("checks", []) if isinstance(report, dict) else report
        second = dict(message, value=value, checks=checks, days=[], historical=not job or message["job_id"] != job["id"],
                      stale=message.get("state") == "stale" or message["base_version"] != context["version"]["number"])
        candidate = next((draft for draft in service.store.all("drafts", owner) if draft["id"] == value.get("draft_id")), {})
        if candidate.get("plan"):
            candidate_version = dict(context["version"], plan=candidate["plan"], checks=report)
            candidate_days = candidate["plan"].get("days", [])
            second["days"] = [_day(candidate_version, day) for day in candidate_days]
    return _render("result", context, title="Trip results", result_page=True, first=first, second=second, job=job,
                   pending=not current_message, today_text=today_text)


__all__ = ["build_site", "Policy", "PrivacyError"]
