"""What a turn puts in front of the agent before it answers (M1; R-79, R-80; D-61 six; D-63 four).

`build` lists, for one question, the memory the answer must weigh: the user's preferences and rules, decisions (with
whether they are a must), people, where they want to go, and this trip's sources and notes. Each entry has its ID, one
sentence, where it comes from and whether it is `stable` (改动要用户本轮明说) or `changing` (可改，留记录，回答里说出改了什么).
The ledger records come from the turn's constraint pack (packs.py), so the people, window and place it narrows to are
the ones this list narrows to; the plan gives people, the destination and places; `sources` gives the rest.

`sources` merges this trip's sources and notes: this trip's `setting` entries, then long-term ones (said as lasting, or
imported from the profile and not refused), then the product's region table (render/regions.yaml). The kinds, their
default stability, how many entries each scenario lists and the language names are data (consider.yaml); a user's
`stability:<kind>` setting overrides a kind's default.

`asks` gives what the answer must ask (R-58 must-go places; R-67 real-time data), `defaults` what the next message
settles when the user did not answer (no must-go places; no real-time source), and `offer` the one line that offers to
draft a question for someone on site (D-61 four) with the drafting spec.
"""
from __future__ import annotations

from functools import cache
from importlib.resources import files

import yaml

SETTING = "setting"
SCENARIOS = ("plan", "replan", "compare", "requirements")  # the scenarios that plan, so ask about must-go and sources
WORDS = {
    "zh": dict(more="另有 {n} 条没列，state(view='memory') 可看。", must="必须", inferred="推断", pending="长期条目待确认，没答前照用",
               next="回答前先逐条看 consider 里的 {n} 条：用到哪条、权衡掉哪条（为什么），回答里说清；标“必须”的不能动，要动先问用户；"
                    "定了基本不变的（stable）只按用户本轮的话改。",
               ask_next="ask 里的问题照问。", people="{id}：{name}", destination="{areas}（{start}—{end}）",
               priority={"must": "必去（计划里标的）", "recommended": "推荐"},
               must_go="有没有必须要去的地方或必须守的时间？没提就当没有。",
               realtime="这趟要不要配实时数据（例：{options}）？不配就用免费的 Transitous，按时刻表。",
               must_go_default="用户没提必须去的地方，按没有算", realtime_default="这趟不配实时数据，用 Transitous（按时刻表）",
               offer="我可以帮你起草询问内容（{languages}）。", join="、", source_on="开", source_off="不配"),
    "en": dict(more="{n} more not listed; state(view='memory') lists them.", must="must", inferred="inferred",
               pending="long-term entry awaiting confirmation, applied until answered",
               next="Before answering, go through the {n} entries in consider: say which you use and which you trade off (and why); "
                    "a must cannot change without asking the user; a stable entry changes only on the user's own words this turn.",
               ask_next="Ask the questions in ask.", people="{id}: {name}", destination="{areas} ({start} to {end})",
               priority={"must": "must-see (marked in the plan)", "recommended": "recommended"},
               must_go="Is there any place you must go to, or a time you must keep? If you don't say, I take it there is none.",
               realtime="Do you want real-time data on this trip (e.g. {options})? Without it I use the free Transitous timetable.",
               must_go_default="The user named no must-go place; taken as none", realtime_default="No real-time data on this trip; Transitous timetable",
               offer="I can draft what to ask, in {languages}.", join=", ", source_on="on", source_off="not set up"),
}


@cache
def table() -> dict:
    data = yaml.safe_load((files(__package__) / "consider.yaml").read_text(encoding="utf-8"))
    kinds = data["kinds"]
    for name, rule in data["scenarios"].items():
        unknown = [kind for kind in rule["kinds"] if kind not in kinds]
        if unknown:
            raise ValueError(f"consider.yaml scenario {name} names unknown kinds {unknown}")
    for name, kind in kinds.items():
        if kind["stability"] not in ("stable", "changing"):
            raise ValueError(f"consider.yaml kind {name} must be stable or changing")
    return data


def words(language: str) -> dict:
    return WORDS["en" if language == "en" else "zh"]


def is_setting(item: dict) -> bool:
    return item.get("kind") == SETTING


def setting_kind(key: str) -> str | None:
    return table()["settings"].get(key.split(":", 1)[0])


def long_term(item: dict) -> bool:
    """Said as lasting (long_term) or imported from the profile into this trip (origin)."""
    return bool(item.get("long_term") or item.get("origin"))


def kind_of(item: dict) -> str:
    """The memory kind of a ledger item (consider.yaml kinds)."""
    if is_setting(item):
        return setting_kind(item["key"]) or "note"
    if long_term(item):
        return "long_term"
    return {"goal": "want_to_go", "preference": "preference"}.get(item["kind"], "rule")


def overrides(items: list[dict]) -> dict[str, str]:
    """The user's `stability:<kind>` settings: this trip's over long-term ones."""
    found: dict[str, str] = {}
    for item in sorted((item for item in items if is_setting(item) and item["key"].startswith("stability:")
                        and item.get("status") in ("active", "pending") and item.get("value") in ("stable", "changing")),
                       key=lambda item: not long_term(item)):
        found[item["key"].split(":", 1)[1]] = item["value"]
    return found


def stability(kind: str, overridden: dict[str, str] | None = None) -> str:
    return (overridden or {}).get(kind) or table()["kinds"][kind]["stability"]


def stable_plan_paths(overridden: dict[str, str] | None = None) -> dict[str, str]:
    """Plan paths that only the user's own words this turn change: {path: kind} for the stable kinds that name some."""
    return {path: kind for kind, rule in table()["kinds"].items() for path in rule.get("plan_paths", [])
            if stability(kind, overridden) == "stable"}


# --- this trip's sources and notes ----------------------------------------------------------------------------------

def regions_of(plan: dict | None) -> list[dict]:
    """The region-table rows this trip touches: rows whose words occur in the trip's or a day's area; else the default."""
    from tripwright.render.regions import table as regions

    said = " ".join([*((plan or {}).get("areas") or []), *(str(day.get("area") or "") for day in (plan or {}).get("days", []))]).casefold()
    rows = [row for row in regions()["regions"] if any(word.casefold() in said for word in row.get("match") or [])]
    return rows or [next(row for row in regions()["regions"] if row["id"] == "default")]


def origin(item: dict) -> dict:
    return {"record": item["id"], "event": (item.get("source") or {}).get("event"), "basis": (item.get("source") or {}).get("basis"),
            "tier": "long_term" if long_term(item) else "trip"}


def settings(store, owner: str, *, at: str) -> list[dict]:
    """Active setting entries of the trip, this trip's first, then long-term ones; imported entries the user said are
    not used on this trip are left out (Ledger.current)."""
    from tripwright.ledger import Ledger

    found = [item for item in Ledger(store).current(owner, at=at)["items"] if is_setting(item) and item["status"] in ("active", "pending")]
    return sorted(found, key=lambda item: long_term(item))


def best(entries: list[dict], key: str, place: str | None = None) -> dict | None:
    """The entry in effect for a key (and a region, for scoped ones): this trip's before long-term, the newest first."""
    matching = [item for item in entries if item["key"] == key and (place is None or (item.get("scope") or {}).get("place", "*") in ("*", place))]
    if place is not None:
        matching.sort(key=lambda item: (item.get("scope") or {}).get("place", "*") == "*")
    return matching[0] if matching else None


def language_names(codes: list[str], language: str) -> list[str]:
    names = table()["languages"]
    key = "en" if language == "en" else "zh"
    return [names.get(code, {}).get(key, code) for code in codes]


def sources(store, owner: str, plan: dict | None, *, at: str, language: str = "zh", region: str | None = None) -> dict:
    """This trip's sources and notes in effect, each with where it comes from: `{regions, entries, languages, apps,
    reports, realtime}`. `region` narrows to one row of the region table (a lookup's), else the trip's regions.
    `reports` are the app choices in the form render.regions.choose reads; `realtime` says whether this trip has
    answered the real-time question and what the region table offers."""
    from tripwright.render.regions import Tool, row as region_row, table as regions

    w = words(language)
    key = "en" if language == "en" else "zh"
    entries = settings(store, owner, at=at)
    overridden = overrides(entries)
    rows = [region_row(region)] if region else regions_of(plan)
    found = []

    def add(entry_id: str, kind: str, text: str, source: dict, **extra):
        found.append(dict(id=entry_id, kind=kind, text=text, stability=stability(kind, overridden), **{"from": source}, **extra))

    for name, product in regions()["sources"].items():
        add(f"source:{name}", "source", f"{product[key]}：{w['source_on']}" if key == "zh" else f"{product[key]}: {w['source_on']}",
            {"table": "regions.yaml", "row": f"sources.{name}"})
    for item in entries:
        if item["key"].startswith("source:") and best(entries, item["key"]) is item:
            add(item["id"], "source", item["text"], origin(item), **status_of(item, store, owner))
    apps, reports = {}, []
    for row in rows:
        chosen = best(entries, "app:timetable", row["id"])
        if chosen and chosen.get("value") in regions()["tools"]:
            apps[row["id"]] = chosen["value"]
            reports.append(dict(kind="user_said", region=row["id"], timetable=chosen["value"], why=chosen["text"], source_event=chosen["id"]))
            add(chosen["id"], "app", chosen["text"], origin(chosen), region=row["id"], **status_of(chosen, store, owner))
        else:
            apps[row["id"]] = row["timetable"]
            tool = Tool(row["timetable"])
            add(f"app:{row['id']}", "app", f"{row['name']}：{tool.app(language)}（{row['why'][key]}）" if key == "zh"
                else f"{row['name']}: {tool.app(language)} ({row['why'][key]})",
                {"table": "regions.yaml", "row": row["id"]}, region=row["id"], status=row["status"])
    chosen = best(entries, "ask_languages")
    if chosen and isinstance(chosen.get("value"), list) and chosen["value"]:
        codes = list(dict.fromkeys(["en", *chosen["value"]]))
        source = origin(chosen)
    else:
        codes = list(dict.fromkeys(["en", *(code for row in rows for code in row.get("languages") or [])]))
        source = {"table": "regions.yaml", "row": "+".join(row["id"] for row in rows)}
    add(source.get("record") or "ask_languages", "ask_languages", w["join"].join(language_names(codes, language)), source, value=codes)
    seen = set()
    for item in entries:
        if item["key"].startswith("note:") and best(entries, item["key"]) is item:
            seen.add(item["text"])
            add(item["id"], "note", item["text"], origin(item), **status_of(item, store, owner))
    for row in rows:
        for index, note in enumerate(row.get("notes") or []):
            if note[key] not in seen:
                seen.add(note[key])
                add(f"note:{row['id']}:{index}", "note", note[key], {"table": "regions.yaml", "row": row["id"]}, status=note.get("status", "inferred"))
    for item in entries:
        if item["key"] == "must_go" or item["key"].startswith("stability:"):
            add(item["id"], setting_kind(item["key"]), item["text"], origin(item), **status_of(item, store, owner))
    options = list({option["id"]: option for row in rows for option in row.get("realtime") or []}.values())
    configured = any(item["key"].startswith("source:") for item in entries)
    return dict(regions=[dict(id=row["id"], name=row["name"]) for row in rows], entries=found, languages=codes, apps=apps,
                reports=reports, realtime=dict(configured=configured, options=[option[key] for option in options]))


def status_of(item: dict, store, owner: str) -> dict:
    """`pending` for an imported long-term entry the user has not confirmed for this trip; `inferred` for the agent's."""
    from tripwright.ledger import Ledger

    if (Ledger(store).carried(owner).get(item["id"]) or {}).get("status") == "pending":
        return dict(status="pending")
    if (item.get("source") or {}).get("basis") == "agent_inference":
        return dict(status="inferred")
    return {}


# --- the list for one question -------------------------------------------------------------------------------------


def build(store, owner: str, *, pack: dict | None, plan: dict | None, scenario: str, at: str, language: str = "zh",
          limit: int | None | str = "scenario") -> dict:
    """`{entries, more, next}` for a turn: the pack's preferences, rules and decisions, the plan's people, destination
    and places, and this trip's sources and notes, in the order of consider.yaml, cut at the scenario's limit. Without
    a pack (state view memory), every current item and decision and everyone; `limit=None` lists all."""
    from tripwright.ledger import Ledger

    w = words(language)
    rule = table()["scenarios"]["scratch" if not plan or pack is None else scenario]
    kinds = set(rule["kinds"])
    overridden = overrides(store.all("items", owner))
    ledger = Ledger(store)
    carried = ledger.carried(owner)
    if pack is None:
        view = ledger.current(owner, at=at)
        item_ids = [item["id"] for item in view["items"] if item["status"] == "active" and not is_setting(item)]
        decision_ids = [decision["id"] for decision in view["decisions"]]
        people = ["*"]
    else:
        item_ids, decision_ids = pack["required"]["items"]["ids"], pack["required"]["decisions"]["ids"]
        people = pack["header"]["people"]
    found = []
    for item_id in item_ids:
        item = store.get("items", owner, item_id)
        kind = kind_of(item)
        extra = dict(must=True) if item["strength"] in ("must", "forbid") else {}
        if (carried.get(item_id) or {}).get("status") == "pending":
            extra["status"] = "pending"
        elif item["source"].get("basis") == "agent_inference":
            extra["status"] = "inferred"
        found.append(dict(id=item_id, kind=kind, text=item["text"], stability=stability(kind, overridden), **{"from": origin(item)}, **extra))
    for decision_id in decision_ids:
        decision = store.get("decisions", owner, decision_id)
        found.append(dict(id=decision_id, kind="decision", text=f"{decision['question']} → {decision['choice']}",
                          stability=stability("decision", overridden), must=bool(decision.get("must")),
                          decided_by=decision["decided_by"], **{"from": origin(decision)}))
    if plan:
        found.append(dict(id=f"trip:{plan['id']}/areas", kind="destination", stability=stability("destination", overridden),
                          text=w["destination"].format(areas=w["join"].join(plan.get("areas") or []), start=plan["start"], end=plan["end"]),
                          **{"from": {"plan": "/areas"}}))
        for index, person in enumerate(plan.get("people", [])):
            if "*" in people or person["id"] in people:
                found.append(dict(id=f"person:{person['id']}", kind="person", text=w["people"].format(id=person["id"], name=person.get("name", person["id"])),
                                  stability=stability("person", overridden), **{"from": {"plan": f"/people/{index}"}}))
        for place in plan.get("places", []):
            if place.get("priority") in ("must", "recommended"):
                found.append(dict(id=f"place:{place['id']}", kind="want_to_go", text=f"{place['name']}（{w['priority'][place['priority']]}）"
                                  if language != "en" else f"{place['name']} ({w['priority'][place['priority']]})",
                                  stability=stability("want_to_go", overridden), **{"from": {"plan": f"/places/{place['id']}"}}))
    found += sources(store, owner, plan, at=at, language=language)["entries"]
    order = table()["order"]
    listed = sorted((entry for entry in found if entry["kind"] in kinds),
                    key=lambda entry: (not entry.get("must"), order.index(entry["kind"])))
    limit = rule["limit"] if limit == "scenario" else limit
    shown, rest = (listed, []) if limit is None else (listed[:limit], listed[limit:])
    result = dict(entries=shown, more=len(rest), next=w["next"].format(n=len(shown)))
    if rest:
        result["more_how"] = w["more"].format(n=len(rest))
    return result


# --- what to ask, and what silence means ----------------------------------------------------------------------------

def _window(scope: dict | None) -> dict:
    return {key: (scope or {}).get(key) for key in ("start", "end") if (scope or {}).get(key)}


def settled(store, owner: str) -> list[dict]:
    """What earlier turns took as answered by silence (`defaults`), kept with their contexts: runtime state, not the
    ledger, so a question the host may never have shown writes no record; a rebuilt store asks once more."""
    return [entry for context in store.all("contexts", owner) for entry in context.get("settled") or []]


def must_go_answered(store, owner: str, scope: dict | None, *, at: str) -> bool:
    """Whether this range (the trip, or the days asked about) has an answer about must-go places: a `must_go` setting
    whose scope contains it, a must the user decided or stated inside it, or silence after the question (`settled`)."""
    from tripwright.ledger import Ledger, Scope, contains, overlaps

    asked = Scope.model_validate(_window(scope))
    if any(entry["id"] == "must_go" and contains(Scope.model_validate(entry.get("scope") or {}), asked) for entry in settled(store, owner)):
        return True
    view = Ledger(store).current(owner, at=at)
    for item in view["items"]:
        if item["status"] not in ("active", "pending"):
            continue
        if is_setting(item) and item["key"] == "must_go" and contains(Scope.model_validate(_window(item.get("scope"))), asked):
            return True
        if item["kind"] == "goal" and item["strength"] == "must" and overlaps(Scope.model_validate(item.get("scope") or {}), asked):
            return True
    return any(decision.get("must") and overlaps(Scope.model_validate(decision.get("scope") or {}), asked) for decision in view["decisions"])


def asks(store, owner: str, plan: dict | None, *, scenario: str, scope: dict | None, at: str, language: str = "zh",
         known: dict | None = None) -> list[dict]:
    """The questions this answer must ask: must-go places when it plans a range not asked about yet (R-58); real-time
    data when the destination is set and this trip has no source setting (R-67). Each says how to record the answer."""
    w = words(language)
    found = []
    planning = scenario in SCENARIOS or not plan
    if planning and not must_go_answered(store, owner, scope, at=at):
        found.append(dict(id="must_go", say=w["must_go"], scope=_window(scope), default=w["must_go_default"],
                          record=dict(items=[dict(kind=SETTING, key="must_go", value="none or named", subject=["*"], scope=_window(scope),
                                                  strength="prefer", quote="<the user's words>", text="<one sentence>",
                                                  source={"event": "this", "basis": "user_explicit"})]),
                          then="Places the user names as musts become user decisions with must: true (propose)."))
    known = known or sources(store, owner, plan, at=at, language=language)
    if plan and planning and not known["realtime"]["configured"] and not any(entry["id"] == "realtime" for entry in settled(store, owner)):
        options = w["join"].join(known["realtime"]["options"]) or "Wiener Linien、VAO"
        found.append(dict(id="realtime", say=w["realtime"].format(options=options), default=w["realtime_default"],
                          record=dict(items=[dict(kind=SETTING, key="source:realtime", value={"on": "true or false", "which": "<id>"},
                                                  subject=["*"], strength="prefer", quote="<the user's words>", text="<one sentence>",
                                                  source={"event": "this", "basis": "user_explicit"})])))
    return found


def defaults(store, owner: str, asked: list[dict], record: dict | None, *, at: str, language: str = "zh") -> list[dict]:
    """What the next message settles by silence when the user did not answer what the last turn asked: no must-go
    places (没提就当没有), no real-time source (不配). Called after this message's own records; each entry
    `{id, scope, text}` goes into this turn's context (`settled`) and the turn's `recorded.defaulted`."""
    w = words(language)
    said = {item.get("key") for item in (record or {}).get("items", []) if isinstance(item, dict)}
    found = []
    for question in asked:
        if question["id"] == "must_go" and "must_go" not in said and not must_go_answered(store, owner, question.get("scope"), at=at):
            found.append(dict(id="must_go", scope=question.get("scope") or {}, text=w["must_go_default"]))
        if question["id"] == "realtime" and not any(str(key).startswith("source:") for key in said) \
                and not any(item["key"].startswith("source:") for item in settings(store, owner, at=at)):
            found.append(dict(id="realtime", scope={}, text=w["realtime_default"]))
    return found


def offer(languages: list[str], language: str = "zh") -> dict:
    """The one line an answer ends with when someone on site has to be asked (D-61 four), and what a draft must hold
    when the user wants one: English and the local languages, names and times as written, nothing invented."""
    w = words(language)
    names = language_names(languages, language)
    return dict(say=w["offer"].format(languages=w["join"].join(names)), languages=languages,
                spec=dict(languages=languages, required="English and every local language listed", sentences_per_language=4,
                          verbatim="place names, line numbers, times and names on bookings exactly as the tool results write them",
                          never="booking numbers, prices or times that no tool result or user message gave",
                          where="in the conversation only; a draft is not recorded"))
