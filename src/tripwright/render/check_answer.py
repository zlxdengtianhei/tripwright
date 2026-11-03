"""Thirteen mechanical checks on a written answer (design 9.1, output-design 1.3 layer four; the thirteenth from the
user's 10-31 requirement that the text, not only the link, says which app to open and what to search).

These are signals for replay scoring and for a host that wants a second look. Passing them does not show that an
answer used the right conditions or gave the right advice; that is judged against the replay set and by judges who
did not write the answer. Sentence length, vague words and English sentence length mostly flag trends, so they are
hints; the other checks are failures.

Run: `python -m tripwright.render.check_answer answer.md [--data answer.json] [--json]` (exit 1 on a failure).

The second section (A6) checks a host's draft of the first or second part against this trip's evidence and the clock:
`check_draft` is what `query(kind="check_answer")` runs before a part is sent, and `scripts/answer_checks.py` scores
replays with the same functions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, tzinfo
from urllib.parse import parse_qs, unquote, unquote_plus, urlsplit

from tripwright.render.first_part import LATER

LIMITS = {"quick": 5, "first": 10, "second": 16}
STEP_VERBS = ("坐", "换", "走", "下车", "买", "出示", "搜")
EN_STEP_VERBS = ("take", "change", "walk", "get off", "buy", "show", "search", "leave", "go")
VAGUE = ("附近", "稍远", "尽快", "大概", "适当", "相关", "有精力的话", "差不多", "一会儿")
EN_VAGUE = ("nearby", "soon", "a bit far", "roughly", "suitable", "relevant", "if you have energy", "approximately", "a while")
PROCESS_START = ("我查了", "我先", "正在", "已提交", "系统", "我已经", "让我", "我来", "好的", "收到")
PROCESS_ANY = ("我查了", "已提交", "系统标记", "系统里", "网站已", "已写入网站")
EN_PROCESS_START = ("i checked", "i have", "i've", "i'll", "i will", "let me", "i'm", "saved", "submitted", "the system",
                    "updating", "recording", "sure", "ok,", "okay", "got it")
APPS = ("Google 地图", "Google Maps", "Apple 地图", "Apple Maps", "ÖBB", "Scotty", "SBB", "DB Navigator", "WienMobil",
        "Bolt", "Uber")
UNCHECKED = ("没核到：", "没核：", "待核：", "Not checked: ", "Not checked:")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
EXPLANATION = re.compile(r"^(?P<text>.+?) · (?:(?P<search>[^·]+?(?:搜 |: search )[^·]+?) · )?\[[^\]]+\]\(https?://[^)]+\)$")
SEARCH = re.compile(r"\S\s?搜 \S|: search \S")
LINK_NAMES = ("destination", "origin", "query", "S", "Z", "source", "address", "name")
SENTENCE_MARKS = re.compile(r"[。，！？：:；]")
TIME = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:—(?:[01]?\d|2[0-3]):[0-5]\d)?\b")
SCHEDULE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?:—(?:[01]\d|2[0-3]):[0-5]\d)? ")
RECORD_ID = re.compile(r"\b(?:I|F|D|P|Q|E|V|L|CTX|DR|J|OP|N)-\d{6}-[a-z0-9]{3,6}\b")
FIELD = re.compile(r"`[a-z_][a-z0-9_.]*`|\b[a-z]+_[a-z0-9_]+\b")
TABLE = re.compile(r"^\s*\|.*\|\s*$|\|\s*:?-{3,}")
LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][\wÀ-ÖØ-öø-ÿ'’.\-]*")
CJK = re.compile(r"[⺀-鿿豈-﫿]")
HINTS = {"sentence_length", "vague", "english_length"}


def finding(check: str, number: int | None, text: str, message: str) -> dict:
    return {"check": check, "severity": "hint" if check in HINTS else "fail", "line": number, "text": text, "message": message}


def detect_language(markdown: str) -> str:
    return "zh" if len(CJK.findall(markdown)) * 4 >= len(re.findall(r"[A-Za-z]", LINK.sub("", markdown))) else "en"


def detect_skeleton(lines: list[str]) -> str:
    if any(line.startswith(("# 第二段", "# Second reply")) for line in lines):
        return "second"
    if any(line.startswith(("# 第一段", "# First reply")) for line in lines):
        return "first"
    return "quick"


def sentences(text: str, language: str) -> list[str]:
    text = LINK.sub("", text)
    parts = re.split(r"[。！？；]" if language == "zh" else r"(?<=[.!?;])\s+", text)
    return [part.strip() for part in parts if part.strip()]


def strip_label(text: str) -> str:
    return re.sub(r"^(?:[-*] |\d+\. )?(?:[^：:\s]{1,6}[：:] ?)?", "", text)


def zh_length(sentence: str, places: list[str]) -> int:
    for name in sorted(places, key=len, reverse=True):
        sentence = sentence.replace(name, "")
    sentence = TIME.sub("", LATIN.sub("", sentence))
    return len(re.sub(r"[\d\s€$%.,，、：:·→()（）「」“”\"'\-—]", "", sentence))


def classify(lines: list[str], places: list[str]) -> dict[int, str]:
    """Kind of each line: blank, heading, notice, name, explanation, schedule, leg, body."""
    kinds: dict[int, str] = {}
    in_schedule = False
    for index, line in enumerate(lines):
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if not line.strip():
            kinds[index] = "blank"
        elif line.startswith("#"):
            kinds[index] = "heading"
            in_schedule = line.startswith(("# 今天的安排", "# Today's plan"))
            continue
        elif in_schedule and SCHEDULE.match(line):
            kinds[index] = "schedule"
        elif EXPLANATION.match(line) and index > 0 and kinds.get(index - 1) == "name":
            kinds[index] = "explanation"
        elif line in places or (EXPLANATION.match(nxt) and not LINK.search(line) and " · " not in line
                                and not SENTENCE_MARKS.search(line) and not SCHEDULE.match(line + " ")):
            kinds[index] = "name"
        elif " → " in line and " · " in line:
            kinds[index] = "leg"
        else:
            kinds[index] = "body"
        if kinds[index] == "blank":
            in_schedule = False
    return kinds


def check_answer(markdown: str, *, skeleton: str | None = None, language: str | None = None, places: list[str] = (),
                 notices: int = 0) -> list[dict]:
    lines = markdown.splitlines()
    language = language or detect_language(markdown)
    skeleton = skeleton or detect_skeleton(lines)
    places = list(places)
    kinds = classify(lines, places)
    for index in range(min(notices, len(lines))):
        kinds[index] = "notice"
    names = places or [lines[index] for index, kind in kinds.items() if kind == "name"]
    answer_text = LINK.sub("", markdown)
    found: list[dict] = []
    body = [index for index, kind in kinds.items() if kind == "body"]

    # 1 行数上限：不计地名行、链接行、标题、通知与第二段的安排全量。
    if len(body) > LIMITS[skeleton]:
        found.append(finding("lines", None, "", f"{len(body)} body lines; the {skeleton} outline allows {LIMITS[skeleton]}"))

    for index, line in enumerate(lines):
        kind = kinds[index]
        number = index + 1
        if kind in ("blank", "heading"):
            continue
        plain = LINK.sub(lambda match: match.group(1), line)
        # 2 句长（中文）与 12 句长（英文）：不计地名、时刻、数字、链接。
        if kind in ("body", "notice", "schedule"):
            for sentence in sentences(strip_label(line), language):
                if language == "zh":
                    step = sentence.startswith(STEP_VERBS)
                    size, limit = zh_length(sentence, names), 20 if step else 30
                    if size > limit:
                        found.append(finding("sentence_length", number, line,
                                             f"{'step' if step else 'explanation'} sentence has {size} characters (limit {limit})"))
                else:
                    words = [word for word in sentence.split() if not TIME.fullmatch(word)]
                    step = sentence.lower().startswith(EN_STEP_VERBS)
                    limit = 20 if step else 25
                    if len(words) > limit:
                        found.append(finding("english_length", number, line,
                                             f"{'step' if step else 'other'} sentence has {len(words)} words (limit {limit})"))
        # 3 模糊词：句里没有数字时标出。
        vague = VAGUE if language == "zh" else EN_VAGUE
        for sentence in re.split(r"[，,]", " ".join(sentences(RECORD_ID.sub("", plain), language))):
            hit = next((word for word in vague if word in sentence.lower()), None)
            if hit and not re.search(r"\d", sentence):
                found.append(finding("vague", number, line, f"vague word {hit!r} without a number"))
        # 4 过程与系统状态词。
        start = strip_label(plain).lower()
        if language == "zh":
            if start.startswith(PROCESS_START) or any(word in plain for word in PROCESS_ANY):
                found.append(finding("process", number, line, "describes process or system state instead of the answer"))
        elif start.startswith(EN_PROCESS_START):
            found.append(finding("process", number, line, "describes process or system state instead of the answer"))
        # 5 内部字段名与编号。
        bare = LINK.sub(lambda match: match.group(1), line)
        leaked = FIELD.findall(bare) + RECORD_ID.findall(bare)
        if leaked:
            found.append(finding("field_names", number, line, f"internal names or IDs: {', '.join(leaked)}"))
        # 6 表格。
        if TABLE.search(line):
            found.append(finding("table", number, line, "tables do not read on a phone; use one line per item"))
        # 8 地名行只放地名；说明行不超过 15 字。
        if kind == "name" and re.search(r"[：:→]|\d{1,2}:\d{2}", line):
            found.append(finding("place_line", number, line, "a place line holds only the place name"))
        if kind == "explanation":
            text = EXPLANATION.match(line).group("text")
            size = len(re.sub(r"[\s·]", "", text)) if language == "zh" else len(text.split())
            if size > 15:
                found.append(finding("place_line", number, line, f"explanation has {size} {'characters' if language == 'zh' else 'words'} (limit 15)"))
        if places and kind in ("body", "notice"):
            for name in places:
                if line.startswith(name) and line != name:
                    found.append(finding("place_line", number, line, f"{name!r} needs its own line"))
        # 9 链接存在：每条交通行有链接或 App 名；链接格式对。
        if kind in ("leg", "schedule") and " → " in line and not LINK.search(line) and not any(app in line for app in APPS):
            found.append(finding("links", number, line, "transport line has neither a link nor an app name"))
        for _, url in LINK.findall(line):
            if len(url) > 2048 or " " in url or ("google.com/maps" in url and "api=1" not in url):
                found.append(finding("links", number, line, f"malformed link {url[:60]}"))
        # 13 交通段与地名说明行要写明在哪个 App 搜什么；链接里的站名、地名、时刻正文里都要有。
        if kind in ("leg", "schedule", "explanation") and LINK.search(line):
            visible = LINK.sub("", line)
            if kind != "schedule" or " → " in line:
                if not SEARCH.search(visible):
                    found.append(finding("search_terms", number, line, "say which app to open and the exact search; the link is only a shortcut"))
            before = lines[index - 1] if kind == "explanation" else ""
            for _, url in LINK.findall(line):
                missing = [value for value in link_values(url) if value not in visible and value not in before
                           and not (TIME.fullmatch(value) and value in answer_text)]
                if missing:
                    found.append(finding("search_terms", number, line, f"only in the link: {', '.join(missing)}"))
        # 10 没核的事写全：什么 → 后果 → 现场怎样确认。
        if line.startswith(UNCHECKED):
            segments = [segment.strip(" 。.") for segment in re.split(r"[：:]", line, maxsplit=1)[-1].split("→")]
            if len(segments) < 3 or not all(segments):
                found.append(finding("unchecked_line", number, line, "write: what → consequence → how to confirm on site"))

    # 7 同一地名只单独占一行一次。
    for name in dict.fromkeys(names):
        count = sum(1 for index, line in enumerate(lines) if line == name and kinds[index] == "name")
        if count > 1:
            found.append(finding("repeated_place", None, name, f"{name!r} stands on its own line {count} times"))
    # 11 第一行是答案。
    content = [index for index, kind in kinds.items() if kind not in ("blank", "heading", "notice")]
    if content:
        index = content[0]
        line = lines[index]
        start = strip_label(LINK.sub("", line)).lower()
        process = start.startswith(PROCESS_START if language == "zh" else EN_PROCESS_START)
        size = zh_length(strip_label(line), names) if language == "zh" else len(line.split())
        limit = 25 if language == "zh" else 20
        if process or line.rstrip().endswith(("？", "?")):
            found.append(finding("first_line", index + 1, line, "the first line must be the answer"))
        elif skeleton == "quick" and size > limit:
            found.append(finding("first_line", index + 1, line, f"the first line has {size} (limit {limit})"))
    return found


def link_values(url: str) -> list[str]:
    """Place names and the clock time a map or timetable link carries; the answer text must carry them too."""
    query = parse_qs(urlsplit(url).query)
    values = [unquote(value).strip() for key in LINK_NAMES for value in query.get(key, [])]
    values += [value for value in query.get("time", []) if TIME.fullmatch(value)]
    return [value for value in values if value]


def failed(found: list[dict]) -> bool:
    return any(entry["severity"] == "fail" for entry in found)


def report(found: list[dict], language: str = "zh") -> str:
    fails = sum(entry["severity"] == "fail" for entry in found)
    hints = len(found) - fails
    head = (f"机械检查（只是信号，不证明回答用对了条件）：{fails} 项不合格，{hints} 条提醒" if language == "zh" else
            f"Mechanical checks (signals only; they do not show the answer used the right conditions): {fails} failures, {hints} hints")
    rows = [f"{'L' + str(entry['line']) if entry['line'] else '--'} [{entry['severity']}:{entry['check']}] {entry['message']}"
            + (f" | {entry['text']}" if entry["text"] else "") for entry in found]
    return "\n".join([head, *rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tripwright.render.check_answer", description=__doc__.splitlines()[0])
    parser.add_argument("answer", help="Markdown file, or - for standard input")
    parser.add_argument("--data", help="the answer data (JSON) the Markdown was rendered from: supplies outline, language, places and notices")
    parser.add_argument("--skeleton", choices=sorted(LIMITS))
    parser.add_argument("--language", choices=("zh", "en"))
    parser.add_argument("--place", action="append", default=[], help="a place name that must stand on its own line")
    parser.add_argument("--notices", type=int, default=0, help="number of leading conflict-notice lines")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    markdown = sys.stdin.read() if args.answer == "-" else open(args.answer, encoding="utf-8").read()
    options = dict(skeleton=args.skeleton, language=args.language, places=args.place, notices=args.notices)
    if args.data:
        data = json.load(open(args.data, encoding="utf-8"))
        options.update(skeleton=args.skeleton or data["skeleton"], language=args.language or data["language"],
                       places=args.place or [place["name_local"] for place in data.get("places", [])],
                       notices=args.notices or len(data.get("notices", [])))
    found = check_answer(markdown, **options)
    print(json.dumps(found, ensure_ascii=False, indent=1) if args.json else report(found, options["language"] or detect_language(markdown)))
    return 1 if failed(found) else 0


# --- A6: a draft checked against this trip's evidence and the clock -----------------------------------------------------
# Each problem is {check, line, text, why, fix}: which check, the draft's line number (1-based; None for the whole draft)
# and its text, why it fails, and one sentence on how to rewrite it. The host fixes the draft and sends it.

CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3])[:：]([0-5]\d)(?![\d:])")
MARKUP = re.compile(r"</?[A-Za-z_][\w-]*(?:\s[^<>]*)?/?>")
# Tool and system states, and the English status lines hosts emit while they wait.
INTERNAL = ("没连上", "没存进", "到哪里之前定", "已收件", "还没生效", "待同步", "Fallback", "Meanwhile", "Still reading",
            "Still checking", "I'm still", "I am still", "Let me ")
SNAKE = re.compile(r"(?<![\w/.=-])[a-z]+(?:_[a-z0-9]+)+(?![\w/.=-])")  # field names: counts_as_checked, latest_departure
LINK_TARGET = re.compile(r"\]\([^)]*\)|https?://\S+")
SECOND_TITLE = re.compile(r"^#+\s*第二段")
MARKED = ("核过", "网上说法", "搜索摘要", "没核", "和第一段不同", "仍没核", "没找到", "查不到")
PLAN_HEADING = re.compile(r"^#{1,6}\s*完整安排")
CHECKED_WORD = re.compile(r"(?<!没)核过")
ONLY = re.compile(r"唯一|只有这一班|只有一班|最后一班|最后一个|末班|首班|头班|之前没有|之后没有|之后再没有|再没有别的|没有更晚|更晚的没有"
                  r"|没有(?:火车|车|巴士|公交|班次|别的车)|没车|最晚|只剩|别指望")
# A8 (D-57): these say something about a whole span, so they need a lookup that covered it. 最晚, 只剩 and 别指望 count
# only in a sentence about getting somewhere (最晚 22:00 前吃完 is not a timetable claim).
SOFT_ONLY = ("最晚", "只剩", "别指望")
TRAVEL = re.compile(r"车|班|走法|出门|出发|动身|坐|搭|火车|巴士|公交|地铁|电车|轻轨|渡轮|船|\bbus\b|\btrain\b", re.I)
EARLIER = re.compile(r"首班|头班|之前没有")
DIRECT = re.compile(r"直达|不用换车|不换车|不用换乘|无需换乘|一趟到")
NOT_CLAIMED = ("没核", "网上说法", "搜索摘要", "查不到", "没查到", "不能说", "没查")
CLAUSES = re.compile(r"[。！？；;!?]")
BOUND = re.compile(r"\s*(?:前|之前|以前|为止)")  # after a time: "21:30 前"
ARRIVE_BOUND = re.compile(r"\s*(?:前|之前|以前)\s*(?:到|抵达)")  # an arrival deadline: "要 05:45 前到"
UNTIL = re.compile(r"(?:到|至|直到)\s*$")  # before a time: "之后到 04:22"
NOW_CLAIM = re.compile(r"现在(?:是|已经是)?\s*(?:凌晨|早上|上午|中午|下午|晚上)?\s*((?:[01]?\d|2[0-3])[:：][0-5]\d)")
EVIDENCE_ID = re.compile(r"\bEV-\d{6}-[a-z0-9]{6}\b")
ISO_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)")
CLOCK_SLACK = 5  # minutes a first part's clock may differ from the moment it is sent
NO_EVIDENCE = dict(times=set(), windows=[], ids=set(), counted=[], reachable=set(), latest=0, sources=[], lookups=[])


def clock_minutes(match: re.Match) -> int:
    return int(match.group(1)) * 60 + int(match.group(2))


def hhmm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def ahead(time: int, clock: int) -> int:
    """How many minutes `time` lies after `clock` on a 24-hour dial, in -720..719."""
    return (time - clock + 720) % 1440 - 720


def numbered(text: str) -> list[tuple[int, str]]:
    """The draft's non-blank lines, stripped, with their 1-based line numbers."""
    return [(number, line.strip()) for number, line in enumerate(text.splitlines(), 1) if line.strip()]


def clock_times(text: str, zone: tzinfo) -> set[int]:
    """Every clock time in a text: local HH:MM, and ISO times with an offset turned into the trip's zone."""
    found = {clock_minutes(match) for match in CLOCK.finditer(ISO_TIME.sub(" ", text))}
    for stamp in ISO_TIME.findall(text):
        local = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(zone)
        found.add(local.hour * 60 + local.minute)
    return found


def shares_words(line: str, texts: list[str], size: int = 4) -> bool:
    bare = re.sub(r"\s+", "", CLOCK.sub("", line))
    pool = re.sub(r"\s+", "", " ".join(texts))
    return any(bare[i:i + size] in pool for i in range(len(bare) - size + 1) if not re.fullmatch(r"[\W\d]+", bare[i:i + size]))


def problem(check: str, line: int | None, text: str, why: str, fix: str) -> dict:
    return dict(check=check, line=line, text=text, why=why, fix=fix)


def clock_problems(text: str, clock: int) -> list[dict]:
    """First part: the first two lines name a time within 5 minutes of the clock, and a time stated as now is the clock
    ("现在 03:43"; not "不用现在就走，最晚 05:23")."""
    lines = numbered(text)[:2]
    head = " ".join(line for _, line in lines)
    near = [match.group(0) for match in CLOCK.finditer(head) if abs(ahead(clock_minutes(match), clock)) <= CLOCK_SLACK]
    wrong = [said for said in NOW_CLAIM.findall(head) if abs(ahead(clock_minutes(CLOCK.search(said)), clock)) > CLOCK_SLACK]
    where = lines[-1] if lines else (None, "")
    found = []
    if not near:
        found.append(problem("clock_in_first_part", where[0], where[1], f"前两行没有接近现在 {hhmm(clock)} 的时刻",
                             f"第二行写“按现在 {hhmm(clock)} 算”，再写要靠的条件（在哪）"))
    if wrong:
        found.append(problem("clock_in_first_part", where[0], where[1], f"写的现在 {'、'.join(wrong)} 和钟 {hhmm(clock)} 差超过 {CLOCK_SLACK} 分钟",
                             f"现在写成 {hhmm(clock)}；用户的说法和钟对不上时照钟，在这行点明"))
    return found


def tag_problems(text: str) -> list[dict]:
    """No markup tags, tool or system states or English status lines."""
    found = []
    for number, line in numbered(text):
        hits = MARKUP.findall(line) + [word for word in INTERNAL if word in line]
        if hits:
            found.append(problem("no_tags", number, line, f"有标签或工具状态句：{'、'.join(dict.fromkeys(hits))}",
                                 "删掉这些字；工具和系统的状态不写给用户"))
    return found


INTERNAL_TERMS = ("外部截止", "计划目标")
# A11 replay: the server's own sentences carried how it searched and a note to the host, and the answer copied both:
# “（4 次查询，每页接着上一页查）”, “（交通那段说一次就够，不用每条都写）”
# A16 (quality judge 8, 3 of 3 runs): the lookup's own words “连续查全，24 班”, “在这次的查询结果里”
PROCESS_NOTES = re.compile(r"\d+\s*次查询|每页接着上一页|查了\s*\d+\s*页|\b\d+ searches\b|each from the last departure found|"
                           r"连续查全|在这次的查询结果里|in this lookup's results", re.I)
WRITING_NOTES = re.compile(r"说一次就够|不用每条都写|每条都写|\bsay it once\b|\bnot on every line\b", re.I)


def internal_problems(text: str) -> list[dict]:
    """No internal words in the answer (A7): the tool's terms 外部截止 and 计划目标, evidence IDs, field names (links are
    not read)."""
    found = []
    for number, line in numbered(text):
        visible = LINK_TARGET.sub(" ", line)
        hits = [term for term in INTERNAL_TERMS if term in visible] + EVIDENCE_ID.findall(visible) + SNAKE.findall(visible)
        if hits:
            found.append(problem("internal_words", number, line, f"有内部用语、证据号或字段名：{'、'.join(dict.fromkeys(hits))}",
                                 "换成用户的话：外部截止写成“柜台几点关”这类具体的事，计划目标写成“计划几点到”；证据号和字段名删掉，"
                                 "来源写网站名或原句"))
    return found


def note_problems(text: str) -> list[dict]:
    """No process talk (how many searches, how the pages were chained) and no writing note meant for the host (A11)."""
    found = []
    for number, line in numbered(text):
        process, writing = PROCESS_NOTES.findall(line), WRITING_NOTES.findall(line)
        if process or writing:
            kinds = "、".join(kind for kind, hits in (("过程话", process), ("写作说明", writing)) if hits)
            found.append(problem("no_process_notes", number, line, f"有{kinds}：{'、'.join(dict.fromkeys(process + writing))}",
                                 "删掉：查了几次、怎么一页页查和写给宿主的说明都不写给用户；只留查到的结果和范围"))
    return found


def title_problems(text: str, clock: int) -> list[dict]:
    """Second part: the title's time (in its closing parentheses) and every "HH:MM 查" are not later than now."""
    found = []
    for position, (number, line) in enumerate(numbered(text)):
        stated = []
        if position == 0:
            closing = re.search(r"[（(]([^（）()]*)[）)]\s*$", line)
            stated += list(CLOCK.finditer(closing.group(1))) if closing else []
        stated += list(re.finditer(CLOCK.pattern + r"\s*查", line))
        late = [match.group(0) for match in stated if 0 < ahead(clock_minutes(CLOCK.search(match.group(0))), clock) <= 720]
        if late:
            found.append(problem("title_time_not_later", number, line, f"写的 {'、'.join(late)} 比现在 {hhmm(clock)} 晚",
                                 f"标题和“几点查”写现在的钟 {hhmm(clock)} 或更早"))
    return found


SECOND_HEADING = re.compile(r"^#{1,6}\s*(?:第二段|second part\b)", re.I)
SECOND_HEADING_FIX = "第一行写 `# 第二段 · …（HH:MM）`"


def second_heading_problems(text: str) -> list[dict]:
    """Second part (D-48): a heading line "第二段" that carries the time it was written, as the skeleton's
    "# 第二段 · 核过了（HH:MM）"; the user and the Stop hook tell the second part by it (a second part that opened with
    "有变化：" passed before). A write-conflict line may come first. title_time_not_later checks that the time is not
    later than now."""
    lines = numbered(text)
    heading = next(((number, line) for number, line in lines if SECOND_HEADING.match(line)), None)
    if heading is None:
        first = lines[0] if lines else (None, "")
        return [problem("second_heading", first[0], first[1], "没有“第二段”标题行", SECOND_HEADING_FIX)]
    if not CLOCK.search(heading[1]):
        return [problem("second_heading", heading[0], heading[1], "“第二段”标题里没有时刻", SECOND_HEADING_FIX)]
    return []


def heading_problems(text: str) -> list[dict]:
    """Second part: the full plan sits under a Markdown heading "完整安排" (the user asked for it to stand out)."""
    lines = numbered(text)
    if any(PLAN_HEADING.match(line) for _, line in lines):
        return []
    plain = next(((number, line) for number, line in lines if line.strip("*#- ").startswith("完整安排")), (None, ""))
    return [problem("plan_heading", plain[0], plain[1], "完整安排没有 Markdown 标题",
                    "把完整安排的标题写成单独一行“## 完整安排”" if plain[0] else "完整安排前加一行“## 完整安排”")]


def fallback_problems(text: str) -> list[dict]:
    """On a line with "误了", the fallback named after it leaves after the choice that was missed: the time right after
    "误了", else the last time in the same clause before it ("04:45 的大巴误了"), else the line's first time."""
    found = []
    for number, line in numbered(text):
        at = line.find("误了")
        if at < 0:
            continue
        end = next((index for index in range(at, len(line)) if line[index] in "。；;!?！？"), len(line))
        after = list(CLOCK.finditer(line, at, end))  # the fallback is named in the same sentence
        clause = max(line.rfind(mark, 0, at) for mark in "。；;") + 1
        before = list(CLOCK.finditer(line[:at], clause)) or list(CLOCK.finditer(line[:at]))[:1]
        missed = after[0] if after and after[0].start() - at <= 4 else (before[-1] if before else None)
        fallback = next((match for match in after if match is not missed
                         and match.start() > (missed.end() if missed and missed.start() > at else at)), None)
        if missed and fallback and ahead(clock_minutes(fallback), clock_minutes(missed)) < 0:
            found.append(problem("fallback_later", number, line, f"误了 {missed.group(0)} 之后的后备 {fallback.group(0)} 比它早开",
                                 f"后备换成比 {missed.group(0)} 晚开的班次（latest_departure 的 fallback）；没有就写没有"))
    return found


# a10d: “23:53 不是末班” denies the claim; it is not a last-train conclusion (A10 replay turn 1 L4)
NEGATED = re.compile(r"(?:不是|并非|不算|不再是|已不是|并不是|算不上)\s*$")
ADJACENT = 6  # characters between 最晚/末班 and the time it names (A10 replay turn 1 L32: “…，最晚 00:53 REX” named 00:53)
NIGHT_BREAK = 120  # minutes without a departure that end the service night: what runs after that is the next day's
LINE_AFTER = re.compile(r"\s*([A-Za-z]{1,8}|\d{1,3}[A-Za-z]?)(?=[\s\d，。；,;）)]|$)")  # “00:53 REX 22787”: the line written with it


def negated(sentence: str, word: re.Match) -> bool:
    return bool(NEGATED.search(sentence[:word.start()]))


def claim_word(sentence: str) -> re.Match | None:
    """The first 唯一/末班/最晚… in a sentence that claims it: not marked 没核 or the like, not denied (“不是末班”), and
    最晚/只剩/别指望 only about getting somewhere."""
    if any(mark in sentence for mark in NOT_CLAIMED):
        return None
    word = next((match for match in ONLY.finditer(sentence) if not negated(sentence, match)), None)
    if word is None:
        return None
    return word if word.group(0) not in SOFT_ONLY or TRAVEL.search(sentence) else None


def adjacent(sentence: str, word: re.Match) -> re.Match | None:
    """The time 最晚/末班 names: the first within ADJACENT characters after it (“最晚 00:53”, “最后一班是 21:59”), else the
    last within ADJACENT characters before it (“23:53 末班”); None when no time is that close."""
    after = next((match for match in CLOCK.finditer(sentence, word.end()) if match.start() - word.end() <= ADJACENT), None)
    if after is not None:
        return after
    before = [match for match in CLOCK.finditer(sentence, 0, word.start()) if word.start() - match.end() <= ADJACENT]
    return before[-1] if before else None


def adjacent_time(sentence: str, word: re.Match) -> int | None:
    found = adjacent(sentence, word)
    return clock_minutes(found) if found else None


def adjacent_line(sentence: str, word: re.Match) -> str | None:
    """The line written right after that time (“最晚 00:53 REX” → REX), which the claim is about."""
    found = adjacent(sentence, word)
    line = LINE_AFTER.match(sentence, found.end()) if found else None
    return line.group(1) if line else None


def said_times(sentence: str) -> tuple[list[int], set[int]]:
    """The clock times a sentence states (not "HH:MM 查"), and those of them that bound its span ("21:30 前", "为止")."""
    said, bounds = [], set()
    for match in CLOCK.finditer(sentence):
        if "查" in sentence[match.end():match.end() + 3]:
            continue
        said.append(clock_minutes(match))
        if BOUND.match(sentence, match.end()) or UNTIL.search(sentence[max(0, match.start() - 4):match.start()]):
            bounds.add(clock_minutes(match))
    return said, bounds


def covering_latest(lookups: list[dict], word: str, said: list[int]) -> bool:
    """A latest_departure lookup that searched until connections miss its target backs "最晚 X" when X is a latest time
    it gives (first choice, routes, later ones), and "the last one"/"nothing after" when the sentence names the last
    connection that still arrives in time; every time in the sentence must be that lookup's."""
    for lookup in lookups:
        if lookup["kind"] != "latest_departure" or not lookup["covers"]:
            continue
        if not said:
            return True
        if not set(said) <= lookup["times"] | lookup["targets"]:
            continue
        backing = lookup["latest"] if word == "最晚" else lookup["last"]
        if set(said) & backing:
            return True
    return False


def later_found(lookups: list[dict], subject: int, bound: int | None, said: set[int], clock: int,
                arrive_by: int | None = None, line: str | None = None, night: bool = False) -> list[str]:
    """Connections the lookups found leaving after `subject` (and before `bound`) that the sentence leaves out. With an
    arrival deadline (`arrive_by`, “要 05:45 前到”, A10 replay), one that arrives after it is no counterexample; with a
    line written next to the time (`line`, “最晚 00:53 REX”), only that line's connections are; with `night`, only those
    before the first NIGHT_BREAK after `subject` (the 04:19 after a 00:53 last train is the next day's, a10d)."""
    shown = []
    for lookup in lookups:
        # a vehicle's time (boarding, a ride) is compared with boarding times; a time to leave with leaving times
        ride = any(subject in (other["board"], *(leg["depart"] for leg in other["rides"])) for other in lookup["connections"])
        for connection in lookup["connections"]:
            if connection["cancelled"] or connection["times"] & said:
                continue
            if line and not any(str(leg["line"] or "").split(" ")[0].casefold() == line.casefold() for leg in connection["rides"]):
                continue
            if arrive_by is not None and (connection["reach"] is None or ahead(connection["reach"], clock) > ahead(arrive_by, clock)):
                continue
            start = connection["board"] if connection["board"] is not None else connection["leave"]
            moment = start if ride else connection["leave"]
            if ahead(moment, clock) > ahead(subject, clock) and (bound is None or ahead(moment, clock) < ahead(bound, clock)):
                name = next((ride["line"] for ride in connection["rides"] if ride["line"]), None)
                shown.append(hhmm(moment) + (f" {name}" if name else ""))
    ordered = list(dict.fromkeys(sorted(shown, key=lambda text: ahead(int(text[:2]) * 60 + int(text[3:5]), clock))))
    if not night:
        return ordered
    tonight, previous = [], ahead(subject, clock)
    for text in ordered:
        at = ahead(int(text[:2]) * 60 + int(text[3:5]), clock)
        if at - previous >= NIGHT_BREAK:
            break
        tonight.append(text)
        previous = at
    return tonight


def earlier_backed(lookups: list[dict], claim: str, subject: int, said: set[int], clock: int) -> bool:
    """"首班 04:22" needs a lookup that found 04:22 and the one before it, named in the sentence ("前一班 00:53"), like
    "末班" needs the next one; "22:00 之前没有" needs a lookup searched from now whose first departure is not before it."""
    for lookup in lookups:
        rows = sorted((connection for connection in lookup["connections"] if not connection["cancelled"]),
                      key=lambda connection: ahead(connection["board"] if connection["board"] is not None else connection["leave"], clock))
        if claim == "之前没有":
            if lookup["start"] is not None and abs(ahead(lookup["start"], clock)) <= CLOCK_SLACK and rows and \
                    ahead(rows[0]["board"] if rows[0]["board"] is not None else rows[0]["leave"], clock) >= ahead(subject, clock):
                return True
            continue
        at = next((index for index, connection in enumerate(rows) if subject in connection["times"]), None)
        if at and rows[at - 1]["times"] & said:
            return True
    return False


def window_problems(text: str, clock: int, windows: list[int], lookups: list[dict] | None = None) -> list[dict]:
    """A sentence that says 唯一, 最后一班, 末班, 只剩, 没有更晚, 之前没有, 首班 or (about getting somewhere) 最晚 or 别指望
    (not marked 没核, 网上说法 or 没查) needs a lookup whose window covers what it says (A8, D-57):
    - its times reach no further than a lookup's window ("查到 HH:MM 为止");
    - with `lookups` (this conversation's connections): nothing it leaves out was found after its time (before its
      bound, "21:30 前"); for 首班 and 之前没有, a lookup searched from before it; a latest_departure lookup that searched
      until connections miss its target backs its own latest and last times; a sentence without a time needs that.
    最晚, 只剩, 别指望 and sentences without a time are read only when this conversation has lookups: without any,
    checked_has_evidence and unreachable_needs_lookup already ask for one. The fix is a sentence the host can copy."""
    found = []
    reach = max(windows, key=lambda to: ahead(to, clock)) if windows else None
    copy = f"写成“查到 {hhmm(reach)} 为止；之后的没查”" if reach is not None else "写成“没查到，现场在 App 里看”"
    for number, line in numbered(text):
        for sentence in (part.strip() for part in CLAUSES.split(line)):
            word = claim_word(sentence)
            if word is None:
                continue
            claim = word.group(0)
            if claim in SOFT_ONLY and not lookups:
                continue
            said, bounds = said_times(sentence)
            if lookups is not None and covering_latest(lookups, claim, said):
                continue
            if not said:
                if lookups and TRAVEL.search(sentence):
                    found.append(problem("only_inside_window", number, line, f"说“{claim}”，没写到几点，这一趟也没有查到底的最晚出发查询",
                                         copy + "；要说就先用 latest_departure 查"))
                continue
            last = max(said, key=lambda time: ahead(time, clock))
            # a window ends at its last leaving time; the vehicle it found boards a few minutes later
            boarded = [connection["board"] for lookup in lookups or [] for connection in lookup["connections"] if connection["board"] is not None]
            if not any(ahead(to, clock) >= ahead(last, clock) for to in [*windows, *boarded]):
                found.append(problem("only_inside_window", number, line,
                                     f"说“{claim}”到 {hhmm(last)}，" + (f"查询窗口只到 {hhmm(reach)}" if reach is not None else "这一趟没有查到这个时刻的查询"),
                                     copy + f"；要说“{claim}”，先用 latest_departure 查到 {hhmm(last)} 以后"))
                continue
            if lookups is None:
                continue
            subjects = [time for time in said if time not in bounds] or said
            subject = min(subjects, key=lambda time: ahead(time, clock))
            near, line = adjacent_time(sentence, word), None
            if near is not None and near in subjects:
                subject, line = near, adjacent_line(sentence, word)  # the time next to the word and its line, not the earliest
            if EARLIER.search(sentence):
                # the time the word names: the first after it ("首班 04:22（前一班 00:53）"), else the last before it
                named = [match for match in CLOCK.finditer(sentence) if match.start() > word.start()] or \
                    [match for match in CLOCK.finditer(sentence) if match.start() < word.start()][-1:]
                subject = clock_minutes(named[0]) if named else subject
                if not earlier_backed(lookups, claim, subject, set(said), clock):
                    found.append(problem("only_inside_window", number, line, f"说“{claim}” {hhmm(subject)}，查询不是从它之前查起的",
                                         f"写成“查到的最早一班是 {hhmm(subject)}”；要说“{claim}”，先从更早查起，并写出“前一班 HH:MM”"))
                continue
            bound = max(bounds, key=lambda time: ahead(time, clock)) if bounds and bounds != set(said) else None
            arrive_by = None
            if bound is not None and any(ARRIVE_BOUND.match(sentence, match.end()) for match in CLOCK.finditer(sentence)
                                         if clock_minutes(match) == bound):
                bound, arrive_by = None, bound  # what leaves later but arrives after the deadline does not count
            later = later_found(lookups, subject, bound, set(said), clock, arrive_by, line, night=True)
            if later:
                found.append(problem("only_inside_window", number, line,
                                     f"说“{claim}”，但工具查到 {hhmm(subject)} 之后还有 {'、'.join(later[:3])}",
                                     f"写查到的班次：{hhmm(subject)}、{'、'.join(later[:3])}；" + copy))
            elif bound is None and arrive_by is None and not later_found(lookups, subject, None, set(), clock):
                # "末班 23:53" from a lookup that stopped at 23:53: nothing after it was searched
                found.append(problem("only_inside_window", number, line, f"说“{claim}” {hhmm(subject)}，查询没查到 {hhmm(subject)} 之后的班次",
                                     f"写成“查到 {hhmm(subject)} 为止；之后的没查”；要说“{claim}”，先查到下一班，并写出“下一班 HH:MM”"))
    return found


# --- A9 (D-64): conclusions about what runs and how many are left need this question's coverage ----------------------
# 别指望/别靠/都误了 conclude a departure does not run; 还有 N 班 counts what is left. “这次没查到” describes a search
# and does not excuse the conclusion; 存档/网上说法/搜索摘要/没核 present a claim without making it.
DENIED = re.compile(r"别指望|别靠|都误了")
COUNTED = re.compile(r"还有[^。；]{0,30}?(?:[零一二两三四五六七八九十]{1,2}\s*[班趟辆]|\d+\s*[班趟辆]|几班)")
COUNT_NUM = re.compile(r"([零一二两三四五六七八九十]{1,2}|\d+)\s*[班趟辆]")
REACH_BOUND = re.compile(r"(?:赶得上|赶不上|不晚于)\s*$")
PRESENTED = ("存档", "网上说法", "搜索摘要", "没核")
CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
GAP_SLACK = 2  # minutes between one lookup's end and the next's start that still read as one covered span


def count_value(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    if word.startswith("十"):
        return 10 + CN_NUM.get(word[1:], 0) if len(word) > 1 else 10
    if word.endswith("十"):
        return CN_NUM.get(word[:-1], 0) * 10
    return CN_NUM.get(word)


def lookup_spans(lookups: list[dict]) -> list[tuple[int, int]]:
    """Each lookup's searched span [start, end] in minutes after midnight: the window's from/to, else its connections'."""
    found = []
    for lookup in lookups or []:
        start, end = lookup.get("start"), lookup.get("end")
        if end is None:
            times = [time for connection in lookup["connections"] for time in (connection["leave"], connection["board"])
                     if time is not None]
            end = max(times) if times else None
        if start is not None and end is not None:
            found.append((start, end))
    return sorted(found)


def covered(time: int, spans: list[tuple[int, int]], clock: int) -> bool:
    return any(ahead(start, clock) - GAP_SLACK <= ahead(time, clock) <= ahead(end, clock) for start, end in spans)


def chain_gap(spans: list[tuple[int, int]], low: int, high: int, clock: int) -> int | None:
    """The first minute of [low, high] no lookup's span covers (a gap between two queries), else None."""
    covered_to, target = ahead(low, clock), ahead(high, clock)
    while covered_to < target:
        nxt = max((ahead(end, clock) for start, end in spans if ahead(start, clock) <= covered_to + GAP_SLACK), default=None)
        if nxt is None or nxt <= covered_to:
            return (clock + covered_to) % 1440
        covered_to = nxt
    return None


def missed_anchor(sentence: str) -> int | None:
    """The time right after 误了 ("误了 22:53"): the departure a count or denial is measured from."""
    at = sentence.find("误了")
    match = CLOCK.search(sentence, at) if at >= 0 else None
    return clock_minutes(match) if match and match.start() - at <= 4 else None


def coverage_problems(text: str, clock: int, lookups: list[dict]) -> list[dict]:
    """A sentence concluding what runs (别指望, 别靠, 都误了) or how many are left (还有 N 班) needs this question's
    lookups to cover what it says (A9, D-64): every time it states inside a lookup's searched span — not in the gap
    between two queries — and a count's span searched without gaps, with nothing the tool found left out. The fix is a
    sentence the host can copy ("查到 HH:MM 为止") or the continuous lookup (transit 的 until)."""
    found = []
    if not lookups:
        return found  # with no lookup at all, checked_has_evidence and unreachable_needs_lookup already ask for one
    spans = lookup_spans(lookups)
    reach = max((ahead(end, clock) for _, end in spans), default=None)
    known = set().union(*(connection["times"] for lookup in lookups for connection in lookup["connections"]))
    for number, line in numbered(text):
        line_said, _ = said_times(line)
        for sentence in (part.strip() for part in CLAUSES.split(line)):
            if any(mark in sentence for mark in PRESENTED):
                continue
            denied, counted = DENIED.search(sentence), COUNTED.search(sentence)
            if not denied and not counted:
                continue
            check, word = ("denied_needs_coverage", denied.group(0)) if denied else ("count_needs_continuous", counted.group(0))
            said, bounds = said_times(sentence)
            anchor = missed_anchor(sentence)
            reach_bounds = {clock_minutes(match) for match in CLOCK.finditer(sentence)
                            if REACH_BOUND.search(sentence, 0, match.start())}
            # outside/gaps are read from the claim's own clause; the completeness of a count may use the whole line
            # (the server's "…还有 N 班…；最早的：…" names the next one in the following clause)
            named = set(said) - bounds - reach_bounds - ({anchor} if anchor is not None else set())
            if not said:
                continue
            located = said if denied and anchor is None else named
            outside = [time for time in sorted(located, key=lambda time: ahead(time, clock)) if not covered(time, spans, clock)]
            if outside:
                last = max(said, key=lambda time: ahead(time, clock))
                found.append(problem(check, number, line,
                                     f"说“{word}”，{'、'.join(hhmm(time) for time in outside)} 落在这次查询的窗口外或两次查询的空档里"
                                     + (f"（查到 {hhmm((clock + reach) % 1440)} 为止）" if reach is not None else ""),
                                     (f"写成“查到 {hhmm((clock + reach) % 1440)} 为止；之后的没查”；" if reach is not None else "")
                                     + f"要说“{word}”，先用连续查法（transit 的 until）从起点连续查到 {hhmm(last)} 以后"))
                continue
            if denied:
                hit = sorted((time for time in said if time in known and time != anchor),
                             key=lambda time: ahead(time, clock))
                if hit:
                    found.append(problem(check, number, line, f"说“{word}”，但工具查到 {hhmm(hit[0])} 这班",
                                         "照查到的写这班还在开；“别指望、别靠”要有它不开或取消的依据，工具没给"))
                continue
            low = anchor if anchor is not None else min(said, key=lambda time: ahead(time, clock))
            reach_bound = max(reach_bounds, key=lambda time: ahead(time, clock)) if reach_bounds else None
            high = max(named, key=lambda time: ahead(time, clock)) if named else (reach_bound or max(said, key=lambda time: ahead(time, clock)))
            gap = chain_gap(spans, low, high, clock)
            if gap is not None:
                found.append(problem(check, number, line,
                                     f"说“{word}”，但 {hhmm(low)}—{hhmm(high)} 之间的查询有空档（{hhmm(gap)} 前后没查）",
                                     f"先用连续查法（transit 的 until={hhmm(high)}）从 {hhmm(low)} 连续查到 {hhmm(high)}，中间不留空档"))
                continue
            departures = {}  # minute -> connection, deduped across overlapping lookups
            for lookup in lookups:
                for connection in lookup["connections"]:
                    if connection["cancelled"]:
                        continue
                    start = connection["board"] if connection["board"] is not None else connection["leave"]
                    if start is None or not ahead(low, clock) < ahead(start, clock) <= ahead(high, clock):
                        continue
                    if reach_bound is not None and connection["reach"] is not None and ahead(connection["reach"], clock) > ahead(reach_bound, clock):
                        continue  # a count “赶得上 HH:MM” only counts what arrives by it
                    departures.setdefault(start, connection)
            missing = []
            for start, connection in sorted(departures.items(), key=lambda entry: ahead(entry[0], clock)):
                if {connection["leave"], connection["board"]} & set(line_said):
                    continue
                line_name = next((ride["line"] for ride in connection["rides"] if ride["line"]), None)
                missing.append(hhmm(start) + (f" {line_name}" if line_name else ""))
            if missing:
                found.append(problem(check, number, line,
                                     f"说“{word}”，但工具查到还有 {'、'.join(missing[:3])} 没写",
                                     f"把查到的班次写全：{'、'.join(missing[:3])}；或用连续查法（transit 的 until）查到的原句"))
                continue
            said_count = COUNT_NUM.search(sentence)
            amount = count_value(said_count.group(1)) if said_count else None
            if amount is not None and len(departures) != amount:
                found.append(problem(check, number, line,
                                     f"说“还有 {amount} 班”，但查到的窗口里这段有 {len(departures)} 班",
                                     "照查到的写有几班，逐班写出时刻；或用连续查法（transit 的 until）查到的原句"))
    return found


def direct_problems(text: str, lookups: list[dict]) -> list[dict]:
    """A sentence that says 直达 or 不用换车 (not marked 没核 or 网上说法) names a connection the lookups found with one
    ride (A8: "13A 直达" was 13A, then a change): matched by its leaving or boarding time, or its last ride's time, or
    without a time by its first line."""
    found = []
    for number, line in numbered(text):
        for sentence in (part.strip() for part in CLAUSES.split(line)):
            word = DIRECT.search(sentence)
            if word is None or any(mark in sentence for mark in NOT_CLAIMED):
                continue
            said = set(said_times(sentence)[0])
            connections = [connection for lookup in lookups for connection in lookup["connections"] if not connection["cancelled"]]
            if said:
                starts = [connection for connection in connections if {connection["leave"], connection["board"]} & said]
                direct = any(len(connection["rides"]) == 1 for connection in starts) or \
                    any(connection["rides"] and connection["rides"][-1]["depart"] in said for connection in connections)
            else:
                starts = [connection for connection in connections if connection["rides"] and connection["rides"][0]["line"]
                          and re.search(r"(?<![\w])" + re.escape(connection["rides"][0]["line"]) + r"(?![\w])", sentence)]
                direct = any(len(connection["rides"]) == 1 for connection in starts)
            if direct:
                continue
            if starts:
                rides = "，换 ".join(f"{hhmm(ride['depart'])} {ride['line'] or ''} {ride['from']} → {ride['to']}".replace("  ", " ")
                                    for ride in starts[0]["rides"])
                found.append(problem("direct_needs_one_ride", number, line,
                                     f"说“{word.group(0)}”，但工具查到这班要换 {len(starts[0]['rides']) - 1} 次车：{rides}",
                                     f"把每一程写出来：{rides}"))
            else:
                found.append(problem("direct_needs_one_ride", number, line, f"说“{word.group(0)}”，这一趟的查询里对不上这班车",
                                     "照工具给的句子写每一程，或去掉“直达”"))
    return found


def acted_times(line: str) -> set[int]:
    """The times a line states as facts: not "HH:MM 查" (when something was checked) and not "现在 HH:MM"."""
    times = set()
    for match in CLOCK.finditer(line):
        if "查" in line[match.end():match.end() + 3] or re.search(r"现在(?:是|已经是)?\s*(?:凌晨|早上|上午|中午|下午|晚上)?\s*$", line[:match.start()]):
            continue
        times.add(clock_minutes(match))
    return times


def evidence_problems(text: str, found: dict) -> list[dict]:
    """Every line that says 核过 (or a list item under a bare 核过 label) matches this trip's evidence: it names an evidence
    ID that counts, or every time it states is in a transit or latest_departure result or in a counted web quote or
    claim, or (without times) it shares words with a counted web claim or quote."""
    web_times = {clock_minutes(match) for match in CLOCK.finditer(" ".join(found["counted"]))}
    problems, section = [], None
    for number, line in numbered(text):
        bare = line.strip("#*-：: ")
        label = next((word for word in MARKED + ("完整安排",) if bare.startswith(word)), None)
        if label:
            section = label
        if line.startswith("#") or bare in MARKED:
            continue  # the title, or a bare label whose items follow
        if not (CHECKED_WORD.search(line) or (section == "核过" and not label and re.match(r"^(?:[-*•]|\d+[.、])\s*", line))):
            continue
        named = set(EVIDENCE_ID.findall(line))
        times = acted_times(line)
        missing = times - found["times"] - web_times
        if named and named <= found["ids"]:
            continue
        if times and not missing:
            continue
        if not times and shares_words(line.split("核过", 1)[-1], found["counted"]):
            continue
        if named - found["ids"]:
            why = f"证据号 {'、'.join(sorted(named - found['ids']))} 不是这一趟算核过的证据"
        elif missing:
            why = f"时刻 {'、'.join(hhmm(time) for time in sorted(missing))} 不在这一趟工具查到的结果里"
        else:
            why = "这一趟没有对得上的工具证据（交通查询、最晚出发或算核过的网页证据）"
        problems.append(problem("checked_has_evidence", number, line, "“核过”对不上证据：" + why,
                                "改成“网上说法（来源）”；要写核过，先用 web_evidence 记下页面原句，这一行写原句和网站名"))
    return problems


def first_line_problems(first: str, second: str, clock: int) -> list[dict]:
    """Every time in the first part's first line, except the clock and a check stamp ("HH:MM 查的", "MM-DD HH:MM 在 …
    查的"), appears again in the second part on a line that says 核过, 网上说法, 没核, 和第一段不同 or 不变, or under such a
    heading. (Replays only: `check_answer` sees one part at a time.)"""
    lines = numbered(first)
    if not lines:
        return []
    number, line_one = lines[0]

    def stamp(match: re.Match) -> bool:
        clause = re.split(r"[，。；,;）)]", line_one[match.end():], maxsplit=1)[0]
        return "查" in line_one[match.end():match.end() + 3] or (bool(re.search(r"\d{1,2}-\d{1,2}\s*$", line_one[:match.start()])) and "查" in clause)

    wanted = {clock_minutes(match) for match in CLOCK.finditer(line_one) if abs(ahead(clock_minutes(match), clock)) > CLOCK_SLACK and not stamp(match)}
    marked, section = [], None
    for _, line in numbered(second):
        bare = line.strip("#*- ")
        label = next((word for word in MARKED + ("完整安排",) if bare.startswith(word)), None)
        if label:
            section = label
        if any(word in line for word in MARKED + ("不变",)) or section in MARKED:
            marked.append(line)
    seen = {clock_minutes(match) for line in marked for match in CLOCK.finditer(line)}
    missing = sorted(wanted - seen)
    if not missing:
        return []
    return [problem("first_line_times_checked", number, line_one, f"第一行的 {'、'.join(map(hhmm, missing))} 第二段没再写",
                    "第二段在核过、没核或和第一段不同里把这些时刻再写一次，变了写“从 HH:MM 改成 HH:MM”")]


NEGATIVE = re.compile(r"赶不上|接不上|来不及|没有车|没车了|没查到能接上|走不通|到不了|坐不上|赶不及")
CONDITIONAL = re.compile(r"如果|要是|万一|假如|一旦|否则|不然|再晚|晚于|之后才|过了")


def unreachable_problems(text: str, found: dict) -> list[dict]:
    """A sentence that says a connection cannot be made (赶不上, 接不上, 来不及, 没有车, 没查到能接上 …) needs this
    conversation's latest_departure result behind it, and a plain one must not name a departure the tool lists as
    reachable (its first choice, later ones, fallback or other candidates). Lines marked 没核 are not claims."""
    problems = []
    for number, line in numbered(text):
        word = NEGATIVE.search(line)
        if not word or "没核" in line:
            continue
        if not found.get("latest"):
            problems.append(problem("unreachable_needs_lookup", number, line, f"说“{word.group(0)}”，这一趟没有最晚出发的查询结果撑",
                                    "先用 latest_departure 查（它给各种走法的候选）；查不到就写“没查到，现场确认”，不写成事实"))
            continue
        if CONDITIONAL.search(line):
            continue
        reachable = [match.group(0) for match in CLOCK.finditer(line) if clock_minutes(match) in found.get("reachable", set())]
        if reachable:
            problems.append(problem("unreachable_needs_lookup", number, line,
                                    f"说“{word.group(0)}”，但工具查到 {'、'.join(reachable)} 那班赶得上",
                                    "照 latest_departure 的候选写哪条赶得上、最晚几点出门；赶不上的要有工具结果"))
    return problems


HUNGRY = re.compile(r"饿|吃|饭|餐|宵夜|夜宵|早点|hungry|\beat\b|food", re.I)
FOOD = re.compile(r"吃|饭|餐|店|麦当劳|汉堡|咖啡|面包|超市|香肠|披萨|快餐|小吃|早点|烤|面|粥|食|Burger|McDonald|Caf[eé]|Kaf|Bäck|Bakery|"
                  r"Restaurant|Imbiss|Würstel|Spar|Billa|Kebab|Pizza|Döner|Starbucks|Subway|KFC|Grill|Kiosk|Snack|Deli|Bistro|Sushi|Wok", re.I)
HOURS = re.compile(r"开到|营业|开门|几点关|关门|全天|24 ?小时|(?:[01]?\d|2[0-3])[:：][0-5]\d\s*[—–~-]\s*(?:[01]?\d|2[0-4])[:：][0-5]\d")
CJK_CHAR = re.compile(r"[⺀-鿿]")


def place_blocks(text: str) -> list[dict]:
    """Places written the answer's way: the name alone on a line, the next line its Chinese name and what it is,
    joined with " · ", and up to two more lines before the next place or heading."""
    lines = numbered(text)
    starts = []
    for index in range(len(lines) - 1):
        name, explanation = lines[index][1], lines[index + 1][1]
        if (" · " in explanation and CJK_CHAR.search(explanation) and " · " not in name and not name.startswith(("#", "-", "*"))
                and not CLOCK.match(name) and not name.startswith(MARKED) and len(name) <= 80 and not re.search(r"[。；！？：:]$", name)):
            starts.append(index)
    blocks = []
    for position, index in enumerate(starts):
        end = min([starts[position + 1] if position + 1 < len(starts) else len(lines), index + 4]
                  + [later for later in range(index + 2, len(lines)) if lines[later][1].startswith("#")])
        blocks.append(dict(line=lines[index][0], name=lines[index][1], explanation=lines[index + 1][1],
                           text="\n".join(line for _, line in lines[index + 1:end])))
    return blocks


def plain_name(text: str) -> str:
    return re.sub(r"[\s'’.,()（）-]+", "", text).casefold()


def meal_problems(text: str, question: str | None, found: dict, places: list[dict] = ()) -> list[dict]:
    """When this question says the user is hungry or wants to eat, the second part names at least two places to eat,
    each with its name line, a Chinese line, the hours it states, and where the hours come from: a place of the trip
    with stored opening hours, or a web evidence record about it."""
    if not question or not HUNGRY.search(question):
        return []
    stored = [plain_name(name) for place in places if place.get("opening_hours") or place.get("hours")
              for name in (place.get("name_local"), place.get("name")) if name]
    named, short = 0, []
    for block in place_blocks(text):
        if not FOOD.search(block["name"] + " " + block["explanation"]):
            continue
        key = plain_name(block["name"])
        sourced = any(key and (key in name or name in key) for name in stored) or shares_words(block["name"], found.get("sources", []))
        hours = bool(HOURS.search(block["text"]))
        if sourced and hours:
            named += 1
        else:
            short.append(f"{block['name']}（{'没写营业时间' if not hours else '营业时间没有出处'}）")
    if named >= 2:
        return []
    return [problem("meals_named", None, "", f"问了吃的，第二段只有 {named} 处具名吃饭地点带营业时间和出处，要至少 2 处"
                    + (f"；不够的：{'、'.join(short)}" if short else ""),
                    "加到至少两处：每处地名一行，下一行写中文名 · 营业时间 · 怎么去；营业时间用地点表里存的（open_places 查），"
                    "或先用 web_evidence 记下官网原句")]


# --- A10 (D-67): the second part's first line keeps the server's frame ------------------------------------------------------
# a9b: the host framed “最晚几点去机场” itself (a train's boarding time read as the airport deadline) and the first line
# said “最晚 21:53 出门”; check_answer only read the sentences. Now a 最晚/末班 conclusion on that line names a time of the
# frame the server computed for this question: tonight's last connection when one was asked (latest_departure without a
# deadline), else the deadline lookups'. Lookups within FRAME_MINUTES of the newest framed one are this question's.
FRAME_WORDS = re.compile(r"最晚|末班|最后一班|最后一个")
FRAME_MINUTES = 30
FRAME_NAMES = {"last_connection": "今晚最后一班", "arrival_deadline": "到达期限", "boarding_time": "赶某班车"}


def frame_problems(text: str, clock: int, lookups: list[dict]) -> list[dict]:
    """The first body line of a second part (after its heading): a 最晚, 末班 or 最后一班 conclusion about getting
    somewhere names a time of this question's server frame (a connection's leaving or boarding time or one of its rides'
    departures; “HH:MM 前” may name a deadline frame's target). With a last-connection frame among this question's,
    only that frame counts: a deadline frame's time on the first line is the wrong frame. The fix is the server's line."""
    framed = [lookup for lookup in lookups or [] if lookup.get("frame") and lookup.get("fetched") is not None]
    if not framed:
        return []
    newest = max(lookup["fetched"] for lookup in framed)
    batch = [lookup["frame"] for lookup in sorted(framed, key=lambda lookup: lookup["fetched"], reverse=True)
             if (newest - lookup["fetched"]).total_seconds() <= FRAME_MINUTES * 60]
    frames = [frame for frame in batch if frame["kind"] == "last_connection"] or batch
    body = next(((number, line) for number, line in numbered(text) if not line.startswith("#")), None)
    if body is None:
        return []
    number, line = body
    for sentence in (part.strip() for part in CLAUSES.split(line)):
        word = next((match for match in FRAME_WORDS.finditer(sentence) if not negated(sentence, match)), None)
        if word is None or not TRAVEL.search(sentence) or any(mark in sentence for mark in NOT_CLAIMED + PRESENTED):
            continue
        times = list(CLOCK.finditer(sentence))
        near = adjacent_time(sentence, word)
        named = ([match for match in times if clock_minutes(match) == near] if near is not None else []) or \
            [match for match in times if match.start() > word.start()] or [match for match in times if match.start() < word.start()][-1:]
        if not named:
            continue
        claimed = clock_minutes(named[0])
        bounded = bool(BOUND.match(sentence, named[0].end()))
        if any(claimed in frame["times"] or (bounded and claimed in frame["targets"]) for frame in frames):
            continue
        wanted = frames[0]
        other = next((frame for frame in batch if frame not in frames and claimed in frame["times"]), None)
        why = (f"首行说“{word.group(0)}” {hhmm(claimed)}，这是{FRAME_NAMES.get(other['kind'], other['kind'])}的时刻；这一问服务端定的框是"
               f"{FRAME_NAMES.get(wanted['kind'], wanted['kind'])}" if other else
               f"首行说“{word.group(0)}” {hhmm(claimed)}，和服务端这一问算出的框（{FRAME_NAMES.get(wanted['kind'], wanted['kind'])}）对不上")
        fix = f"首行照抄服务端的句子：{wanted['first_line']}" if wanted.get("first_line") else "首行照抄 latest_departure 的 first_line"
        return [problem("frame_matches_server", number, line, why, fix)]
    return []


FIRST_LINES = 8


def first_length_problems(text: str) -> list[dict]:
    """The first part has at most 8 body lines; place name lines and their explanation lines do not count, nor does the
    line that ends a reply with it (`first_part.later`, S4a2)."""
    skipped = {number for block in place_blocks(text) for number in (block["line"], block["line"] + 1)}
    skipped |= {number for number, line in numbered(text) if is_later(line)}
    body = [(number, line) for number, line in numbered(text) if number not in skipped and not line.startswith("#")]
    if len(body) <= FIRST_LINES:
        return []
    number, line = body[FIRST_LINES]
    return [problem("first_part_lines", number, line, f"第一段正文 {len(body)} 行，超过 {FIRST_LINES} 行（地名行、说明行不算）",
                    "只留答案、按现在几点算、要靠的条件、没核的一两件；其余放第二段")]


# --- A12: the first part answers this question's points; the second part is short -------------------------------------------
# quality judge 6: the first part was the same airport template for “饿了、想看万圣节、是不是要赶去机场” (A); “07:05 证件柜台关”
# went out as a fact (6); the second part ran to about 20 body lines (F, quality card 3). The turn keeps the points the host
# read from the question (packs.point_record); the first part names each, says where the user is in its first two lines
# and marks every stored time it quotes.
EAT_WORDS = re.compile(r"吃|饭|餐|饿|麦当劳|超市|快餐|\beat\b|\bfood\b|restaurant", re.I)
TICKET_WORDS = re.compile(r"票|\bticket|\bfare", re.I)
GO_WORDS = re.compile(r"最晚|出发|出门|末班|最后一班|不用赶|\blatest\b|\bleave\b|\blast\b", re.I)
ASSUMED = ("假设", "没说在哪", "assuming", "did not say where")
MARKS = ("存档", "没核", "计划里", "存的", "查过", "估的", "archived", "not checked", "stored", "checked at", "estimated")


def names_point(text: str, point: dict) -> bool:
    """A first part answers a point when it names it: its words or a run of three of them, a place it stored for it,
    or (food, tickets, getting somewhere) the word for it."""
    folded = text.casefold()
    if any(key.casefold() in folded for key in point["keys"]) or shares_words(text, [point["said"]], size=3):
        return True
    return bool({"eat": EAT_WORDS, "ticket": TICKET_WORDS, "go": GO_WORDS}.get(point["kind"], re.compile(r"(?!)")).search(text))


def point_problems(text: str, record: dict | None) -> list[dict]:
    """With the turn's points (`record`, packs.point_record): every point named (answers_each_point); where the user is
    named in the first two body lines, or the assumption said there (place_in_first_lines)."""
    if not record or not record.get("given"):
        return []
    found = []
    missing = [point for point in record["points"] if not names_point(text, point)]
    if missing:
        found.append(problem("answers_each_point", None, "", "这一问点名的" + "、".join(f"“{point['said']}”" for point in missing)
                             + "第一段没回应", "每件一行：存的照 turn 的 first_part 写（带标），没存的写“…：第二段给”"))
    body = [(number, line) for number, line in numbered(text) if not line.startswith("#")]
    top = " ".join(line for _, line in body[:2])
    at = record.get("at") or {}
    if at and not at.get("assumed"):
        named = any(key and key.casefold() in top.casefold() for key in (at.get("name"), at.get("said"))) or shares_words(top, [at["said"]], size=3)
        why, fix = f"用户说了在哪（“{at['said']}”），前两行没写", f"第二行写“按现在 HH:MM 算，你在 {at.get('name') or at['said']}”"
    else:
        named = any(word in top for word in ASSUMED) or bool(at.get("name") and at["name"] in top)
        why, fix = "用户没说在哪，前两行没写按哪里算", "第二行写“按现在 HH:MM 算，假设你在 X”" if at.get("name") is None else \
            f"第二行写“按现在 HH:MM 算，假设你在 {at['name']}”"
    if not named:
        number, line = body[min(1, len(body) - 1)] if body else (None, "")
        found.append(problem("place_in_first_lines", number, line, why, fix))
    return found


PLAN_HERE = re.compile(r"计划里(?:这时|此刻|现在)你?在这|where the plan has you (?:now|at this time)", re.I)


def plan_place_problems(text: str, here: dict | None) -> list[dict]:
    """A line that says where the plan has the user now (“计划里这时在这”) names the place the plan has them at this
    moment, from their own blocks (`here`: {name, names}; A14, quality judge 7: “假设你在<午饭的店>（计划里这时在这）”
    while the plan had the gym) (assumed_place_matches_plan)."""
    found = []
    for number, line in numbered(text):
        if not PLAN_HERE.search(line):
            continue
        if here and any(name.casefold() in line.casefold() for name in here["names"]):
            continue
        name = (" " + here["name"]) if here and here["name"][:1].isascii() else (here or {}).get("name")
        why, fix = ((f"写了“计划里这时在这”，计划里这时在{name}", f"写“假设你在{name}（计划里这时在这；不在的话说一声）”")
                    if here else ("写了“计划里这时在这”，计划里这时没有你的块", "写“你没说在哪”，不说计划里这时在哪"))
        found.append(problem("assumed_place_matches_plan", number, line, why, fix))
    return found


def archived_problems(text: str, record: dict | None) -> list[dict]:
    """A stored time no source checked (a boarding node, an operator's or unmarked time; the turn's record) or an
    estimated stored price, quoted without a mark on its line (archived_marked)."""
    if not record:
        return []
    found = []
    for number, line in numbered(text):
        if any(mark in line for mark in MARKS):
            continue
        times = {clock_minutes(match) for match in CLOCK.finditer(line)}
        node = next((entry for entry in record.get("archived", []) if clock_minutes(CLOCK.search(entry["time"])) in times
                     and shares_words(line, [entry["text"]], size=3)), None)
        # an estimate said as one (A16: “约 €2.30”) is marked
        fare = next((amount for amount in record.get("fares", []) if amount in line
                     and not re.search(r"(?:约|about)\s*€?\s*" + re.escape(amount), line, re.I)), None)
        if node or fare:
            what = f"{node['time']} {node['text']}" if node else fare
            found.append(problem("archived_marked", number, line, f"“{what}”是存的、没核过，这行没标",
                                 "这行末尾加“（存档，没核）”，或先查再写核过"))
    return found


# a12b replay: “和第一段不同：计划里写的 22:42 RJX 19959 这次没查到，以下面的班次为准” while the 22:42 RJX was among the
# departures of that very lookup; a train not in the results is not gone either
MISSING = re.compile(r"没查到|查不到|没找到|没有这班|这班没了")
INSTEAD = re.compile(r"以下面的?(?:班次|车次)?为准")
SPAN_AFTER = re.compile(r"\s*(?:那班|这班)?\s*(?:之后|以后|后|之前|以前|前)")  # “误了 22:53 那班之后…没查到”: later ones, not that one
PIECES = re.compile(r"[，,。；;！？!?]")
MISSING_FIX = ("照 latest_departure 的句子写：在结果里的写“计划里的 HH:MM 线路 赶得上：…”；不在的写“这次没在时刻表数据里找到，"
               "要坐它先在 App 核一下”，不说以下面的为准")


def missing_ride_problems(text: str, lookups: list[dict]) -> list[dict]:
    """A train said not to be found that this question's lookups found (a ride leaving at that minute, under the line
    named next to the time when one is; the time and “没查到” in one clause, not “22:53 之后”), or a not-found train
    followed by “以下面的为准” (missing_ride_found)."""
    found = []
    for number, line in numbered(text):
        if line.startswith("#") or not MISSING.search(line):
            continue
        hit = None
        said = [match for piece in PIECES.split(line) if MISSING.search(piece) for match in CLOCK.finditer(piece)
                if not SPAN_AFTER.match(piece, match.end())]
        for match in said:
            minute, named = clock_minutes(match), LINE_AFTER.match(match.string, match.end())
            hit = next(((ride, connection) for lookup in lookups or [] for connection in lookup["connections"] for ride in connection["rides"]
                        if ride["depart"] == minute and (named is None or named.group(1).casefold() in str(ride.get("line") or "").casefold().split())),
                       None)
            if hit:
                break
        if hit:
            ride, connection = hit
            found.append(problem("missing_ride_found", number, line, f"说 {hhmm(ride['depart'])} {ride.get('line') or ''} 没查到，这一问的查询结果里有这班"
                                 + (f"（{hhmm(connection['leave'])} 出门）" if connection.get("leave") is not None else ""), MISSING_FIX))
        elif said and INSTEAD.search(line):
            found.append(problem("missing_ride_found", number, line, "说那班没查到，又说以下面的为准：读的人会以为那班没了", MISSING_FIX))
    return found


# A14 (quality judge 7, 3 of 3 runs): “怎么查” searched the night's last train (00:53) while the answer's first choice was
# the plan's 22:42; the user would check the wrong train's delays. The server's frame keeps the connection its
# how_to_check searches (`main`).
HOW_HEADING = re.compile(r"^#{1,6}\s*(?:怎么查|how to check)", re.I)
SEARCH_TIME = re.compile(r"(?:出发时间填|时间填|departure time|time=)\s*((?:[01]?\d|2[0-3])[:：][0-5]\d)", re.I)
MISSED = ("误了主选", "If you miss the first choice")


# A16 (quality judge 8, 3 of 3 runs): the user ate at the station and “怎么查” searched the bus stop by the gym at 22:28; the
# search is the first choice's first ride, its stop and its time (the frame's `main_first`).
SEARCHED = re.compile(r"搜\s*(?P<zh>[^→，,。；]+?)\s*→[^，,。；]*?[，,]\s*出发时间填\s*(?P<zh_time>(?:[01]?\d|2[0-3])[:：][0-5]\d)|"
                      r"search (?P<en>[^→,;]+?) → [^,;]*?, departure time (?P<en_time>(?:[01]?\d|2[0-3]):[0-5]\d)|"
                      r"[?&]S=(?P<url>[^&\s)]+)[^\s)]*?[?&]time=(?P<url_time>(?:[01]?\d|2[0-3]):[0-5]\d)", re.I)


def stop_key(name: str) -> str:
    return re.sub(r"\s+", " ", unquote_plus(name)).strip().casefold()


def search_problems(text: str, lookups: list[dict]) -> list[dict]:
    """The “怎么查” section searches the first choice the server's frame names: from the stop of its first ride at that
    ride's time (A16; older frames: one of its times, A14). A “误了主选，查末班” line is the second search and does not
    count (search_matches_main)."""
    framed = [lookup for lookup in lookups or [] if lookup.get("frame") and lookup["frame"].get("main") and lookup.get("fetched") is not None]
    if not framed:
        return []
    frame = max(framed, key=lambda lookup: lookup["fetched"])["frame"]
    first = frame.get("main_first")
    if not first and frame["main"] <= frame["last"]:  # the first choice is the last one: nothing else to search
        return []
    section, searched = False, []
    for number, line in numbered(text):
        if line.startswith("#"):
            section = bool(HOW_HEADING.match(line))
            continue
        if not section or line.startswith(MISSED):
            continue
        for match in SEARCHED.finditer(line):
            stop = match.group("zh") or match.group("en") or match.group("url")
            said = match.group("zh_time") or match.group("en_time") or match.group("url_time")
            searched.append((number, line, stop_key(stop), clock_minutes(CLOCK.search(said))))
        if not SEARCHED.search(line):
            searched += [(number, line, None, clock_minutes(CLOCK.search(match.group(1)))) for match in SEARCH_TIME.finditer(line)]
    if not searched:
        return []
    if first:
        wrong = [(number, line, stop, minute) for number, line, stop, minute in searched
                 if minute != first["depart"] or (stop is not None and stop != stop_key(first["stop"]))]
        if not wrong:
            return []
        number, line, stop, minute = wrong[0]
        stop = next((unquote_plus(match.group("zh") or match.group("en") or match.group("url")).strip() for match in SEARCHED.finditer(line)), None)
        return [problem("search_matches_main", number, line, f"“怎么查”搜的是 {hhmm(minute)}" + (f" 从 {stop} 上车" if stop else "")
                        + f"，这一问的主选第一程是 {hhmm(first['depart'])} 从 {first['stop']} 上车",
                        f"搜 {first['stop']} → 终点，出发时间填 {hhmm(first['depart'])}；末班另起一行“误了主选，查末班：……”"
                        "（照 latest_departure 的 how_to_check）")]
    if any(minute in frame["main"] for _, _, _, minute in searched):
        return []
    number, line, _, minute = searched[0]
    train = hhmm(max(frame["main"]))
    return [problem("search_matches_main", number, line, f"“怎么查”搜的是 {hhmm(minute)}，这一问的主选是 {train} 那班",
                    f"出发时间填主选 {train}；末班另起一行“误了主选，查末班：……”（照 latest_departure 的 how_to_check）")]


# A16 (quality judge 8, 3 of 3 runs): “核过：<岩馆> 周六 22:00 关门” and “<计划的车>（22:11 出门）” in one answer.
LEAVE_AT = re.compile(r"((?:[01]?\d|2[0-3])[:：][0-5]\d)\s*出门|\bleave (?:by |at )?((?:[01]?\d|2[0-3]):[0-5]\d)", re.I)


def closing_problems(text: str, lookups: list[dict]) -> list[dict]:
    """A time to leave the origin that this question's lookups found from it, later than the origin closes (the frame's
    `closes`, from its stored hours, kept when the lookup started while it was open): leave_before_closing."""
    framed = [lookup["frame"] for lookup in lookups or [] if (lookup.get("frame") or {}).get("closes") is not None]
    found = []
    for number, line in numbered(text):
        if line.startswith("#"):
            continue
        for match in LEAVE_AT.finditer(line):
            minute = clock_minutes(CLOCK.search(match.group(1) or match.group(2)))
            late = next((frame for frame in framed if minute in frame["leaves"]
                         and ahead(minute, frame["start"]) > ahead(frame["closes"], frame["start"])), None)
            if late:
                found.append(problem("leave_before_closing", number, line, f"写 {hhmm(minute)} 出门，起点 {hhmm(late['closes'])} 就关门",
                                     f"照 latest_departure 的句子写“{hhmm(late['closes'])} 关门出来，走到 … 坐 HH:MM 的车”，"
                                     "或写从关门时刻起算的那班"))
                break
    return found


# A16 (quality judge 8): “€9.70/人” for someone travelling alone.
PER_PERSON = re.compile(r"/人|每人|一人\s*€|\bper person\b|\beach\b(?= ?€)", re.I)


def party_problems(text: str, lookups: list[dict], points: dict | None) -> list[dict]:
    """A price per person when the stored fares are each for one person (the turn's points, else this question's
    lookups): per_person_alone."""
    sizes = [(points or {}).get("party")] + [(lookup.get("frame") or {}).get("party") for lookup in lookups or []]
    sizes = [size for size in sizes if size]
    if not sizes or max(sizes) != 1:
        return []
    return [problem("per_person_alone", number, line, "一个人出行，价钱后面写了“/人”", "去掉“/人”，照 tickets 的句子写")
            for number, line in numbered(text) if not line.startswith("#") and PER_PERSON.search(line)][:1]


# A16 (quality judge 8, off_r1): without the tools the host's first part opened with the plan's “最晚 21:50 … 出门” and
# “最后一班是 23:53 REX”; the lookup later found the last train at 00:53. Before this question's lookup a first part
# quotes no last or latest time, unless from an earlier lookup it marks “HH:MM 查过”.
LATEST_WORD = re.compile(r"最晚|末班|最后一班|最后一趟|\blast (?:train|bus|connection|one)\b|\blatest\b", re.I)
LOOKED = re.compile(r"(?:[01]?\d|2[0-3])[:：][0-5]\d\s*查过|checked at", re.I)


def unlooked_latest_problems(text: str) -> list[dict]:
    """A first part's line with a last/latest word next to a time (within ADJACENT characters either side) and no
    “查过” of an earlier lookup: latest_needs_lookup."""
    found = []
    for number, line in numbered(text):
        if LOOKED.search(line):
            continue
        for word in LATEST_WORD.finditer(line):
            near = line[max(0, word.start() - ADJACENT):word.end() + ADJACENT + 2]
            if CLOCK.search(near):
                found.append(problem("latest_needs_lookup", number, line, f"第一段写了“{word.group(0)}”和时刻：这一问还没查过",
                                     "只给存档的车并标“存档，没核”（照 turn 的 first_part）；最晚、末班第二段查完再给"))
                break
    return found


# A14 (quality judge 7, on_r2): “火车最后一班 00:53” on the first line and “没核：01:00 之后的火车没查，不能说 00:53 之后没有
# 火车” on the last. The second came from a transit lookup to 01:00 (the answer layer's old “tonight”); latest_departure
# had searched to 03:00 without a gap.
SHORT_SAID = re.compile(r"((?:[01]?\d|2[0-3])[:：][0-5]\d)\s*(?:之后|以后)[^。；，,]{0,12}?(?:没查|没核|没搜)|"
                        r"不能说\s*((?:[01]?\d|2[0-3])[:：][0-5]\d)\s*(?:之后|以后)就?没有")


def short_coverage_problems(text: str, clock: int, lookups: list[dict]) -> list[dict]:
    """A line that says the departures after HH:MM were not searched, or that one cannot say none run after HH:MM,
    while this question's search of tonight's last connection covered past HH:MM without a gap (coverage_said_short)."""
    spans = lookup_spans([lookup for lookup in lookups or [] if (lookup.get("frame") or {}).get("kind") == "last_connection"])
    found = []
    for number, line in numbered(text):
        for match in SHORT_SAID.finditer(line):
            said = match.group(1) or match.group(2)
            minute = clock_minutes(CLOCK.search(said))
            reach = [end for start, end in spans if ahead(start, clock) - GAP_SLACK <= ahead(minute, clock) < ahead(end, clock)]
            if reach:
                end = max(reach, key=lambda end: ahead(end, clock))
                found.append(problem("coverage_said_short", number, line, f"说 {said} 之后没查，这一问连续查到了 {hhmm(end)}",
                                     f"照 latest_departure 的窗口句写“连续查到 {hhmm(end)}”；别拿另一次更短的查询说之后没查"))
                break
    return found


SECOND_LINES = 12
URL = re.compile(r"https?://")


def second_length_problems(text: str) -> list[dict]:
    """The second part has at most 12 body lines (quality card 3): place name lines and their explanation lines, lines
    with a link and the items under “## 完整安排” do not count (its 没核 lines do)."""
    skipped = {number for block in place_blocks(text) for number in (block["line"], block["line"] + 1)}
    body, section = [], ""
    for number, line in numbered(text):
        if line.startswith("#"):
            section = line
            continue
        if number in skipped or URL.search(line) or (PLAN_HEADING.match(section) and not line.startswith(UNCHECKED)):
            continue
        body.append((number, line))
    if len(body) <= SECOND_LINES:
        return []
    number, line = body[SECOND_LINES]
    return [problem("second_part_lines", number, line, f"第二段正文 {len(body)} 行，超过 {SECOND_LINES} 行（地名行、说明行、"
                    "带链接的行和“完整安排”各项不算）", "首行答所问，再写和第一段不同的与没核的一两件；同一件事只说一次；"
                    "走法、时刻放进“## 完整安排”，每项一行")]


FIRST_CHECKS = ("clock_in_first_part", "no_tags", "internal_words", "no_process_notes", "first_part_lines", "answers_each_point",
                "place_in_first_lines", "archived_marked", "assumed_place_matches_plan", "latest_needs_lookup", "per_person_alone",
                "no_status_line", "ends_with_later")
SECOND_CHECKS = ("second_heading", "frame_matches_server", "checked_has_evidence", "only_inside_window", "direct_needs_one_ride",
                 "unreachable_needs_lookup", "denied_needs_coverage", "count_needs_continuous",
                 "fallback_later", "plan_heading", "meals_named", "title_time_not_later", "no_tags", "internal_words",
                 "no_process_notes", "second_part_lines", "missing_ride_found", "search_matches_main", "coverage_said_short",
                 "leave_before_closing", "per_person_alone")


def check_draft(text: str, part: str, clock: int, found: dict | None = None, *, question: str | None = None,
                places: list[dict] = (), points: dict | None = None, here: dict | None = None) -> list[dict]:
    """The problems of a first or second part drafted at `clock` (minutes after local midnight), against `found`: the
    times, search windows (their ends), evidence IDs, counted web claims and quotes, reachable departures and web
    sources of this conversation's evidence; `question` is what the user asked, `places` the trip's places, `points`
    what the turn kept of this question's points (A12), `here` where the plan has the user now (A14)."""
    found = dict(NO_EVIDENCE, **(found or {}))
    if part == "first":
        return (clock_problems(text, clock) + tag_problems(text) + internal_problems(text) + note_problems(text) + first_length_problems(text)
                + point_problems(text, points) + archived_problems(text, points) + plan_place_problems(text, here)
                + unlooked_latest_problems(text) + party_problems(text, [], points) + later_problems(text))
    return (second_heading_problems(text) + frame_problems(text, clock, found["lookups"]) + evidence_problems(text, found)
            + window_problems(text, clock, found["windows"], found["lookups"])
            + direct_problems(text, found["lookups"]) + unreachable_problems(text, found)
            + coverage_problems(text, clock, found["lookups"])
            + fallback_problems(text) + heading_problems(text) + meal_problems(text, question, found, places)
            + title_problems(text, clock) + tag_problems(text) + internal_problems(text) + note_problems(text)
            + second_length_problems(text) + missing_ride_problems(text, found["lookups"]) + search_problems(text, found["lookups"])
            + short_coverage_problems(text, clock, found["lookups"]) + closing_problems(text, found["lookups"])
            + party_problems(text, found["lookups"], points))


# --- A7: what was sent is what was checked (the Stop hook) ----------------------------------------------------------------

PART_HEADING = re.compile(r"^#{1,6}\s*(?:第二段|完整安排)", re.M)
# A14 (quality judge 7, off_r1): a second part sent before it passed was checked when the hook asked, then sent again; the
# user got two identical messages. What was sent is on the user's screen: it is checked as it stands, never sent twice.
SEEN = "；这段用户已经看到了：过了就结束，别再发一遍；没过只发一条“更正：”，写改了的几行"
BLOCK_REASON = "第二段和核过的草稿不一致，请把发出的文字交 check_answer 再核" + SEEN
NEVER_REASON = "这一问的第二段还没过 check_answer，请把发出的文字交 check_answer 再核" + SEEN


def is_second_part(text: str) -> bool:
    return bool(PART_HEADING.search(text))


def draft_digest(text: str) -> str:
    """The same text whatever its line endings, trailing spaces and surrounding blank lines."""
    import hashlib

    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return hashlib.sha256("\n".join(lines).strip("\n").encode("utf-8")).hexdigest()


def wall(stamp: str | None) -> float | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else None


def real_message(entry: dict) -> bool:
    """A message the user typed: not a tool result, a meta entry, a subagent's entry, or a task notification Claude
    Code delivers as a user entry when background work ends (`origin.kind` "task-notification", measured on 2.1.301)."""
    if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
        return False
    if "notification" in str((entry.get("origin") or {}).get("kind", "")):
        return False
    content = (entry.get("message") or {}).get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in blocks):
        return False
    text = " ".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict))
    return not text.lstrip().startswith(("<task-notification>", "<system-reminder>"))


def turn_texts(transcript: list[dict]) -> tuple[float | None, list[tuple[float | None, str]]]:
    """When the last real user message of a Claude Code transcript was sent (Unix seconds), and the texts the main
    agent showed after it, each with when it was shown."""
    asked, shown = None, []
    for entry in transcript:
        if real_message(entry):
            asked, shown = wall(entry.get("timestamp")) or asked, []
        elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
            content = (entry.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                    shown.append((wall(entry.get("timestamp")), block["text"]))
    return asked, shown


def answers_later(transcript: list[dict]) -> bool:
    """Whether the last real user message answers a reply that ended with first_part.later (S4e): the first part of
    this question is in that reply, and this message asks for its second part (dry_cc_ns_synth_v4: the first-part hook
    denied the lookups after “继续”, and the host sent its first part again)."""
    before, shown, answered = [], [], False
    for entry in transcript:
        if real_message(entry):
            before, shown = shown, []
            lines = [line for line in (before[-1].splitlines() if before else []) if line.strip()]
            answered = bool(lines) and is_later(lines[-1])
        elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
            content = (entry.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                    shown.append(block["text"])
    return answered


def stop_decision(payload: dict, answers: dict, transcript: list[dict], minute_at=None, blocked: int = 0) -> tuple[str | None, str]:
    """The Stop hook's verdict when the host ends a turn: (reason to block, or None; what to log). A pusher with a cap
    (D-48): it keeps a question going until its second part is out, at most STOP_BLOCKS times per question.

    - The text just sent is a second part (a "第二段" or "完整安排" heading): it must equal the last second part
      check_answer passed during this question.
    - No second part yet in this question (since the last real user message; a task notification is not one):
      - the main agent dispatched a subagent or looked something up, and no text has a first part's shape
        (`first_part_shape` at `minute_at`: the clock in the first two lines, no tags; D-46, D-47): write the first part;
      - a text with a first part's shape says a second part follows (第二段, 核完再发, "second part"), or the main agent
        dispatched or looked something up: unless a check dispatched in the background is still running (no
        notification for its tool use yet), dispatch the check and write the second part.
    - A quick answer (nothing promised, nothing looked up) and a chat pass.
    - `blocked` counts this question's blocks so far (all three reasons together); from STOP_BLOCKS on it lets the
      turn end and logs it. `stop_hook_active` no longer lets a block through by itself."""
    asked, shown = turn_texts(transcript)
    sent = payload.get("last_assistant_message") if isinstance(payload.get("last_assistant_message"), str) else None
    text = sent if sent is not None else (shown[-1][1] if shown else None)
    if text and is_second_part(text):
        passed = [entry for entry in answers.values() if isinstance(entry, dict) and (asked is None or entry.get("wall", 0) >= asked)]
        digest = draft_digest(text)
        if any(entry.get("sha256") == digest for entry in passed):
            return None, "matches a passed draft"
        reason = BLOCK_REASON if passed else NEVER_REASON
        verdict = "differs from the passed draft" if passed else "no passed draft for this question"
    else:
        if minute_at is None:
            return None, "not a second part"
        texts = shown + ([(None, sent)] if sent and sent.strip() and not any(sent.strip() == shown_text.strip() for _, shown_text in shown) else [])
        if any(is_second_part(shown_text) for _, shown_text in texts):
            return None, "not a second part; this question's second part was sent"
        looked = turn_lookups(transcript)
        if answers_later(transcript):  # the user said “继续” to a first part that ended with later: the second part is due
            last, promised, ended = [], True, None
        else:
            last = first_part_sent(texts, minute_at, first_part_shape)
            promised = any(PROMISE.search(shown_text) and not first_part_shape(shown_text, minute_at(at)) for at, shown_text in texts)
            ended = later_ending(texts)
        if looked and last != []:
            reason = STOP_FIRST_REASON + failing(last)
            verdict = f"{', '.join(dict.fromkeys(looked))} this question but no first part"
        elif ended == "ends":
            return None, "first part ends with first_part.later: the user's next message brings the second part"
        elif ended == "more":
            reason = LATER_REASON
            verdict = "text after first_part.later"
        elif promised or looked:
            running = running_checks(transcript)
            if running:
                return None, f"first part sent; {len(running)} background check(s) running"
            reason = PUSH_REASON
            verdict = ("second part promised" if promised else f"{', '.join(dict.fromkeys(looked))} this question") + ", no second part, no check running"
        else:
            return None, "not a second part; nothing promised or looked up"
    if blocked >= STOP_BLOCKS:
        return None, f"would block ({reason}) but let through after {blocked} blocks for this question"
    return reason, "blocked: " + verdict


# --- S4a2: a first part that ends the reply with first_part.later ------------------------------------------------------------
# In an app that cannot show text between tool calls the first part is the whole reply and its last line says how to get
# the second (core step 4). That line ends it: nothing after it, and no status line in it (a17b_off_r2: “第二段核完就发。”;
# a17_off_r3: “第二段我还在核…核完马上发。”). The server's own “没核：……，第二段核完再发。” is not a status line.
LATER_LINES = tuple(LATER.values())
LATER_MARKS = str.maketrans("", "", "“”\"「」『』'‘’ 　")


def later_key(line: str) -> str:
    """The later line as words: quote marks, spaces and the end mark do not count (dry_cc_ns_synth: the host wrote
    回我一句"继续" with ASCII quotes)."""
    return line.strip().translate(LATER_MARKS).rstrip("。.!！")


LATER_KEYS = frozenset(later_key(line) for line in LATER_LINES)


def is_later(line: str) -> bool:
    return bool(line.strip()) and later_key(line) in LATER_KEYS
STATUS_LINE = re.compile(r"马上(?:发|回|给|再)|稍等|稍后(?:发|给|再)|正在(?:查|核|看|找)|还在(?:查|核|看|找)|(?:我|这就)先?去(?:查|核|看)|"
                         r"核完(?:就|马上)发|查完(?:就|马上|再)发|第一段(?:已经)?发(?:完|给你)|上面(?:那段|这段)|"
                         r"I'm still|I am still|[Ss]till checking|[Ll]et me (?:check|look)|I'll (?:check|look)|[Cc]hecking now")
LATER_REASON = "第一段以“回我一句“继续””那一行结束这一轮，后面不再写别的，也不写进度"


def status_lines(text: str) -> list[tuple[int, str]]:
    """Lines that report progress instead of answering (the server's own 没核 line is not one)."""
    return [(number, line) for number, line in numbered(text)
            if STATUS_LINE.search(line) and not line.lstrip().startswith(("没核", "没查", "Not checked"))]


def later_problems(text: str) -> list[dict]:
    """A first part with first_part.later ends with it; no first part carries a status line."""
    found = [problem("no_status_line", number, line, f"第一段里有状态句：{line.strip()}",
                     "删掉这一行：用户只看到第一段和第二段，不看进度") for number, line in status_lines(text)]
    marks = [number for number, line in numbered(text) if is_later(line)]
    after = [(number, line) for number, line in numbered(text) if marks and number > marks[0] and line.strip()]
    if after:
        found.append(problem("ends_with_later", after[0][0], after[0][1], "“回我一句“继续””那一行之后还写了字",
                             "以那一行结束这一轮，后面不写别的"))
    return found


def later_ending(texts: list[tuple]) -> str | None:
    """How this question's shown texts use first_part.later: "ends" when the last one ends with it and has no status line,
    "more" when a text has it and something follows (or a status line comes with it), None when no text has it."""
    with_later = [index for index, (_, text) in enumerate(texts) if any(is_later(line) for line in str(text).splitlines())]
    if not with_later:
        return None
    text = str(texts[with_later[-1]][1])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if with_later[-1] == len(texts) - 1 and lines and is_later(lines[-1]) and not status_lines(text):
        return "ends"
    return "more"


def evidence_found(records: list[dict], zone: tzinfo, since: datetime, until: datetime) -> dict:
    """What recent evidence can back a 核过 line: transit leg times; latest_departure's first choice, later ones and
    fallback (not its target, which is the plan's own time); the ends of search windows (a latest_departure window that
    reaches its limit reaches the limit); the claims and quotes of web evidence that counts as checked; and the IDs of
    all of these. Demo, stale and uncounted records (search snippets, quotes not on the page) never count."""
    from tripwright.evidence import uncounted

    found = dict(times=set(), windows=[], ids=set(), counted=[], reachable=set(), latest=0, sources=[], lookups=[])
    for record in records:
        try:
            fetched = datetime.fromisoformat(record["fetched_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if not since <= fetched <= until or record.get("synthetic") or record.get("stale") or str(record.get("source", "")).startswith("demo:"):
            continue
        if record.get("kind") == "web":  # a place's hours may come from any recorded page or snippet (meals_named)
            found["sources"].append(f"{record.get('url', '')} {record.get('claim', '')} {record.get('quote', '')}")
        if uncounted(record):
            continue
        kind, value = record.get("kind"), record.get("value")
        if kind == "transit":
            found["times"] |= clock_times(json.dumps(value), zone)
            window = record.get("window") or {}
            found["windows"] += sorted(clock_times(str(window.get("to") or ""), zone))
            if isinstance(value, list):
                found["lookups"].append(transit_lookup(value, window, zone))
        elif kind == "latest_departure" and isinstance(value, dict):
            taken = json.dumps([value.get("primary"), value.get("fallback"), value.get("later"), value.get("candidates")])
            found["times"] |= clock_times(taken, zone)
            found["reachable"] |= clock_times(taken, zone)
            found["latest"] += 1
            window = value.get("window") or {}
            ends = [window.get("to")] + ([window.get("target")] if window.get("covers_target") else [])
            found["windows"] += sorted(time for end in ends if end for time in clock_times(str(end), zone))
            found["lookups"].append(dict(latest_lookup(value, zone), fetched=fetched))
        elif kind == "web":
            found["counted"].append(f"{record.get('claim', '')} {record.get('quote', '')}")
        else:
            continue
        found["ids"].add(record["id"])
    return found


def minute(value: str | None, zone: tzinfo) -> int | None:
    """Minutes after local midnight of "HH:MM" or an ISO time."""
    if not value:
        return None
    if ISO_TIME.fullmatch(value):
        local = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(zone)
        return local.hour * 60 + local.minute
    match = CLOCK.fullmatch(value)
    return clock_minutes(match) if match else None


def connection_row(leave: int | None, reach: int | None, rides: list[dict], cancelled: bool = False, in_time: bool | None = None) -> dict:
    board = rides[0]["depart"] if rides else None
    times = {time for time in (leave, reach, board) if time is not None} | {ride[key] for ride in rides for key in ("depart", "arrive")
                                                                           if ride[key] is not None}
    return dict(leave=leave, reach=reach, board=board, rides=rides, cancelled=cancelled, in_time=in_time, times=times)


def transit_lookup(itineraries: list[dict], window: dict, zone: tzinfo) -> dict:
    """A transit record's connections in minutes, with where its window starts."""
    connections = []
    for itinerary in itineraries:
        legs = itinerary.get("legs") or []
        if not legs:
            continue
        rides = [dict(line=leg.get("line"), depart=minute(leg.get("depart"), zone), arrive=minute(leg.get("arrive"), zone),
                      **{"from": leg.get("origin"), "to": leg.get("destination")}) for leg in legs if leg.get("mode") != "walk"]
        connections.append(connection_row(minute(legs[0].get("depart"), zone), minute(legs[-1].get("arrive"), zone), rides,
                                          any((leg.get("extra") or {}).get("cancelled") for leg in legs)))
    return dict(kind="transit", connections=connections, start=minute(window.get("from"), zone),
                end=minute(window.get("to"), zone), covers=False, targets=set(),
                times=set().union(*(connection["times"] for connection in connections)), latest=set(), last=set())


def latest_lookup(value: dict, zone: tzinfo) -> dict:
    """A latest_departure record's connections in minutes: every departure it found (A8; older records: the ones it
    shows), its target times, the latest times it gives, and the last connection that still arrives in time."""
    def from_view(view: dict, in_time: bool | None = None) -> dict:
        rides = [dict(line=leg.get("line"), depart=minute(leg.get("depart"), zone), arrive=minute(leg.get("arrive"), zone),
                      **{"from": leg.get("from"), "to": leg.get("to")}) for leg in view.get("legs", []) if not leg.get("walk")]
        return connection_row(minute(view.get("leave"), zone), minute(view.get("reach"), zone), rides, bool(view.get("cancelled")),
                              in_time if in_time is not None else view.get("within_limit"))

    shown = [view for view in [value.get("primary"), value.get("fallback"), *(value.get("later") or []), *(value.get("candidates") or [])] if view]
    if value.get("departures") is not None:
        connections = [connection_row(minute(row["leave"], zone), minute(row["reach"], zone),
                                      [dict(ride, depart=minute(ride["depart"], zone), arrive=minute(ride["arrive"], zone)) for ride in row["rides"]],
                                      bool(row.get("cancelled")), row.get("in_time")) for row in value["departures"]]
    else:
        connections = [from_view(view) for view in shown]
    window, target = value.get("window") or {}, value.get("target") or {}
    in_time = [connection for connection in connections if connection["in_time"] and not connection["cancelled"]]
    latest = set().union(*({connection["leave"], connection["board"]} for connection in
                           (from_view(view) for view in [value.get("primary"), *(value.get("later") or []), *(value.get("candidates") or [])] if view)))
    covers = bool(window.get("covers_target"))
    end = minute(window.get("to"), zone)
    if covers:
        # it searched until connections miss the target: every departure that could arrive in time was found
        target_minute = minute(target.get("limit"), zone) or minute(target.get("plan_by"), zone) or minute(target.get("time"), zone)
        if target_minute is not None:
            end = target_minute if end is None else max(end, target_minute)
    frame = value.get("frame")
    last = {time for time in (in_time[-1]["leave"], in_time[-1]["board"]) if time is not None} if in_time else set()
    if frame:
        first = frame.get("main_first")
        frame = dict(frame, times={minute(time, zone) for time in frame.get("times", [])} - {None},
                     targets={minute(time, zone) for time in frame.get("targets", [])} - {None},
                     last={minute(time, zone) for time in frame.get("last", [])} - {None},
                     main={minute(time, zone) for time in frame.get("main", [])} - {None},
                     main_first=dict(first, depart=minute(first["depart"], zone)) if first else None,
                     closes=minute(frame.get("closes"), zone), start=minute(frame.get("start"), zone),
                     leaves={minute(time, zone) for time in frame.get("leaves", [])} - {None})
        if frame["kind"] == "last_connection":
            last = frame["last"]  # tonight's last connection(s), as the server framed them (A10)
    return dict(kind="latest_departure", connections=connections, start=minute(window.get("from"), zone),
                end=end, covers=covers,
                targets={time for key in ("time", "plan_by", "limit") for time in [minute(target.get(key), zone)] if time is not None},
                times=set().union(*(connection["times"] for connection in connections)) if connections else set(),
                latest={time for time in latest if time is not None}, last=last, frame=frame)


if __name__ == "__main__":
    raise SystemExit(main())


# --- A7 (D-45): the first part before any lookup (a PreToolUse hook) ------------------------------------------------------

DISPATCH_TOOLS = {"Agent", "Task"}
LOOKUP_TOOLS = DISPATCH_TOOLS | {"WebSearch", "WebFetch"}
LOOKUP_COMMAND = re.compile(r"\btripwright\b[^\n|;&]*\bquery\b")
# S4a (self replays without MCP): `tripwright --repo . query --help` was denied as a lookup in a17b_off_r1 and a17_off_r2,
# and the deny's “dispatch in the background” came right before the status line; reading the help looks nothing up. A
# lookup through a shell alias (`$W query latest_departure`, a17_off_r1) still is one.
COMMAND_PARTS = re.compile(r"&&|\|\||[;|\n]")
HELP = re.compile(r"(?:^|\s)(?:--help|-h)(?=\s|$)")
KIND_COMMAND = re.compile(r"\bquery\s+(?:transit|latest_departure|web_evidence|open_places|nearby|weather|opening|saved|geocode)\b")
FIRST_REASON = "先把第一段发给用户（现在怎么走；没查过的最晚、末班不写），再查"
# D-46: the order that kept both parts (v2e, 16 questions) dispatches the researcher in the background, then writes the
# first part as the turn's last text; the reason points there (it replaces 11-01's "dispatch in the same reply").
IN_BACKGROUND = "核查请在后台派 researcher（Agent 加 run_in_background: true）"
FIRST_DENIALS = 3  # after this many denials for one question the hook lets lookups through, so it cannot lock the host out
STOP_FIRST_REASON = "把第一段写成回复文字再结束（现在怎么走；没查过的最晚、末班不写）"
PUSH_REASON = "在后台派 researcher 核查，再写第二段（先过 check_answer）"
STOP_BLOCKS = 3  # per question, all of the Stop hook's reasons together; then it lets the turn end (D-48)
# A first part that says a second part follows (skeleton: "没核：……，第二段核完再发", "第二段确认"; "the second part follows")
PROMISE = re.compile(r"第二段|核完再发|second part", re.I)
TOOL_USE_ID = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")


def lookup_use(name: str, arguments: dict) -> bool:
    """A lookup: dispatching a subagent, Tripwright's query (not its check_answer), a web search or fetch, or the
    tripwright query command line. turn, reading files and loading a skill are not."""
    if name in LOOKUP_TOOLS:
        return True
    if name.startswith("mcp__") and name.endswith("__query") and "tripwright" in name:
        return arguments.get("kind") != "check_answer"
    if name == "Bash":
        return lookup_command(str(arguments.get("command") or ""))
    return False


def lookup_command(command: str) -> bool:
    """A shell command that runs a lookup in one of its parts: the tripwright query command line (not its check_answer,
    not its help) or a query of a lookup kind through an alias."""
    return any((LOOKUP_COMMAND.search(part) or KIND_COMMAND.search(part)) and "check_answer" not in part and not HELP.search(part)
               for part in COMMAND_PARTS.split(command))


def in_background(name: str, arguments: dict) -> bool:
    return name in DISPATCH_TOOLS and arguments.get("run_in_background") is True


def lookup_call(payload: dict) -> bool:
    """A lookup the first part must come before: one of the main agent's own (a subagent's calls name `agent_id`), and
    not a subagent dispatched in the background (D-46), whose report comes after the first part anyway."""
    if payload.get("agent_id"):
        return False
    name = str(payload.get("tool_name") or "")
    arguments = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    return lookup_use(name, arguments) and not in_background(name, arguments)


def notification(entry: dict) -> str | None:
    """The text of a task notification Claude Code writes when background work ends (2.1.301: a user entry with
    `origin.kind` "task-notification", text opening with <task-notification>, naming the <tool-use-id>), else None."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    text = " ".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict))
    noted = "notification" in str((entry.get("origin") or {}).get("kind", ""))
    return text if noted or text.lstrip().startswith("<task-notification>") else None


def running_checks(transcript: list[dict]) -> list[str]:
    """The subagents the main agent dispatched in the background during this question whose notification has not come
    yet, by tool-use ID (a notification without one ends the oldest)."""
    running: list[str] = []
    for entry in transcript:
        if real_message(entry):
            running = []
        elif (told := notification(entry)) is not None:
            ended = TOOL_USE_ID.findall(told)
            running = [use for use in running if use not in ended] if ended else running[1:]
        elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
            content = (entry.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if in_background(str(block.get("name") or ""), arguments):
                        running.append(str(block.get("id") or ""))
    return running


def turn_lookups(transcript: list[dict]) -> list[str]:
    """The lookups the main agent made since the last real user message (background dispatches too), by tool name."""
    made = []
    for entry in transcript:
        if real_message(entry):
            made = []
        elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
            content = (entry.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if lookup_use(str(block.get("name") or ""), arguments):
                        made.append(str(block.get("name")))
    return made


def first_part_problems(text: str, clock: int) -> list[dict]:
    """The first part's checks: the clock in the first two lines, no tags or tool states, no internal words, 8 lines."""
    return clock_problems(text, clock) + tag_problems(text) + internal_problems(text) + first_length_problems(text)


def first_part_shape(text: str, clock: int) -> list[dict]:
    """What makes a text a first part for the Stop hook (D-47): the clock in the first two lines, no tags or tool
    states. Internal words and length are left to check_answer and the replay checks: holding a turn for them made the
    host write its first part twice (v2e: 9 of 25 stops)."""
    return clock_problems(text, clock) + tag_problems(text)


def first_part_decision(payload: dict, transcript: list[dict], minute_at, denied: int = 0) -> tuple[str | None, str]:
    """The PreToolUse hook's verdict on a tool call: (reason to deny, or None; what to log). A lookup of the main agent
    waits until a text shown after the user's last message passes the first part's checks; `minute_at(wall)` gives the
    clock (minutes after local midnight in the trip's zone) at the moment a text was shown; `denied` counts this
    question's denials so far."""
    if not lookup_call(payload):
        arguments = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        if not payload.get("agent_id") and in_background(str(payload.get("tool_name") or ""), arguments):
            return None, f"background dispatch ({payload.get('tool_name')})"
        return None, f"not a lookup ({payload.get('tool_name')})"
    asked, shown = turn_texts(transcript)
    if asked is None:
        return None, "no user message in the transcript"
    if answers_later(transcript):
        return None, "first part sent in the reply that ended with first_part.later"
    last = first_part_sent(shown, minute_at)
    if last == []:
        return None, "first part sent"
    if denied >= FIRST_DENIALS:
        return None, f"no first part, but let through after {denied} denials"
    reason = FIRST_REASON + failing(last) + "；" + IN_BACKGROUND
    return reason, f"blocked {payload.get('tool_name')}: " + ("no text yet" if last is None else ", ".join(entry["check"] for entry in last))


def first_part_sent(shown: list[tuple[float | None, str]], minute_at, problems=first_part_problems) -> list[dict] | None:
    """[] when one of the shown texts passes `problems` (the first part's checks) at the clock of its moment; else the
    last text's failing checks, or None when nothing was shown."""
    last = None
    for at, text in shown:
        last = problems(text, minute_at(at))
        if not last:
            return []
    return last


def failing(last: list[dict] | None) -> str:
    return "；还没有给用户的文字" if last is None else "；最后一段文字没过：" + "；".join(f"{entry['check']} {entry['why']}" for entry in last)
