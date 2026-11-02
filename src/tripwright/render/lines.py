"""The one-line shapes hosts copy unchanged: a transport leg and a place (CONTRACTS 3.4, design 9.3).

The answer text carries what the user needs even if they never tap a link: which app to open and exactly what to type
there, in the local spelling (user requirement 10-31: "不管点不点链接，正文都要给出要用的信息，并写清在那个 App 里搜什么").
The link is a shortcut to the same search.

- A leg: `verb · <app> 搜 <from> → <to> · timing · note · [label](url)` when the app takes both ends; otherwise
  `verb · from → to · <app> 搜 <query> · timing · note · [label](url)`. Empty parts are left out.
- A place: the name alone on one line (so it can be copied), then `explanation · <app> 搜 <query> · [label](url)`.

`transit_leg` and `place_line` build the data with the app and link chosen by the region table, so the host never
writes a link or a search term itself.
"""
from __future__ import annotations

from tripwright.render import links
from tripwright.render.regions import Tool, choose


def link_text(link: dict) -> str:
    return f"[{link['label']}]({link['url']})"


def search_text(search: dict, language: str = "zh") -> str:
    app, query = search["app"], search["query"]
    if language == "en":
        return f"{app}: search {query}"
    return f"{app}{' ' if app[-1].isascii() else ''}搜 {query}"


def leg_text(leg: dict, language: str = "zh") -> str:
    route = f"{leg['from']} → {leg['to']}"
    found = search_text(leg["search"], language)
    middle = [found] if leg["search"]["query"] == route else [route, found]
    parts = [leg["verb"], *middle, leg.get("timing"), leg.get("note"), link_text(leg["link"])]
    return " · ".join(part for part in parts if part)


def place_lines(place: dict, language: str = "zh") -> list[str]:
    return [place["name_local"], f"{place['line']} · {search_text(place['search'], language)} · {link_text(place['link'])}"]


def transit_leg(verb: str, origin: str, destination: str, *, mode: str, region: str | None = None, day: str | None = None,
                clock: str | None = None, timing: str | None = None, note: str | None = None, reports: list[dict] = (),
                search: str | None = None, language: str = "zh") -> dict:
    """A leg with its search and link: the region's timetable tool for public transport, the map app otherwise.

    A timetable app takes both ends, so the search is `origin → destination`; a map app starts from the phone's
    position, so the search is the destination (or `search`, when the name alone is ambiguous). `day` and `clock`
    only reach tools that accept them."""
    tool = choose(region, reports=reports).timetable if mode in links.PUBLIC else Tool("google-maps")
    maps = tool.id in ("google-maps", "apple-maps")
    query = (search or destination) if maps else f"{origin} → {destination}"
    return dict(verb=verb, **{"from": origin, "to": destination}, timing=timing, note=note,
                search={"app": tool.app(language), "query": query},
                link=leg_link(tool.id, origin, query if maps else destination, mode=mode, day=day, clock=clock,
                              language=language))


def leg_link(tool: str, origin: str, destination: str, *, mode: str, day: str | None, clock: str | None, language: str) -> dict:
    if tool == "oebb-scotty" and day and clock:
        return {"label": links.label("oebb-scotty", language), "url": links.scotty_query(origin, destination, day, clock)}
    if tool == "apple-maps":
        return {"label": links.label("apple-maps", language), "url": links.apple_directions(destination, mode=mode)}
    return {"label": links.label("google-maps", language), "url": links.google_directions(destination, mode=mode)}


def place_line(place_id: str, name_local: str, line: str, *, search: str | None = None, mode: str = "transit",
               maps: str = "google-maps", language: str = "zh") -> dict:
    """A place with a directions link from the phone's position. `search` is the text to type in the map when the
    local name alone is ambiguous (for example "Uferweg Nord, Seeufer"); it is written out in the answer as well."""
    query = search or name_local
    tool = Tool("apple-maps" if maps == "apple-maps" else "google-maps")
    if tool.id == "apple-maps":
        link = {"label": links.label("apple-maps", language), "url": links.apple_directions(query, mode=mode)}
    else:
        link = {"label": links.label("google-maps", language), "url": links.google_directions(query, mode=mode)}
    return dict(place_id=place_id, name_local=name_local, line=line, search={"app": tool.app(language), "query": query},
                link=link)


def timetable_place(place_id: str, name_local: str, line: str, *, origin: str, region: str | None, day: str, clock: str,
                    reports: list[dict] = (), language: str = "zh") -> dict:
    """A stop reached by public transport in a region whose timetable tool takes a query link (for example a bus stop
    in the lake district with ÖBB Scotty): the search names both ends, and the departure time stays in the answer."""
    leg = transit_leg("", origin, name_local, mode="bus", region=region, day=day, clock=clock, reports=reports,
                      language=language)
    return dict(place_id=place_id, name_local=name_local, line=line, search=leg["search"], link=leg["link"])
