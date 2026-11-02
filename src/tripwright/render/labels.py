"""Words and clock formats shared by every renderer.

One table per language keeps one concept to one word (writing rule 6): a label changes here, not in each renderer.
Clock text is always local to the trip's IANA timezone and carries its abbreviation where a reader could misread it.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

# A9 (D-64): a stored "末班 / 只剩 / 最晚 / 没有更晚" sentence is the plan's archive, not a current fact; renderers mark
# it 存档/没核, and the answer never takes it without a fresh lookup.
ARCHIVE_WORDS = re.compile(r"末班|最后一班|只剩|没有更晚|来不及|别指望")


def split_claims(text: str) -> list[str]:
    """A stored text in claim-sized segments: sentences split at 。； and at full-width parentheses, so
    “证件柜台关（误了 22:42，只剩 23:53 末班）” marks only the parenthetical."""
    return [part for part in re.split(r"(?<=[。；）])|(?=[（])", text or "") if part]


def claim_parts(text: str) -> list[str]:
    """The segments of a stored text (an anchor's own words) that carry a last/only/late claim."""
    return [part for part in split_claims(text) if ARCHIVE_WORDS.search(part)]

ZH = {
    "strength": {"must": "必须", "forbid": "禁止", "prefer": "最好", "optional": "可选"},
    "basis": {"user_explicit": "用户明说", "direction_set": "用户定了方向", "recommended_default": "推荐默认",
              "agent_inference": "推断"},
    "decided_by": {"user": "用户定", "agent": "agent 定"},
    "scenario": {"quick": "快问", "status": "上报实况", "replan": "重排第一段", "plan": "从零计划", "compare": "比较",
                 "requirements": "整理需求", "system": "系统问题"},
    "wide": "深核",
    "channel": {"*": "全部通道", "conversation": "对话", "site": "网站", "plan": "计划"},
    "all_places": "全部地点",
    "all_day": "全天",
    "omitted": {"out_of_scope:people": "人不在范围", "out_of_scope:time": "时段外", "out_of_scope:date": "日期外",
                "out_of_scope:place": "地点外", "out_of_scope:channel": "通道外", "over_budget": "超预算"},
    "group": {"items": "条目", "facts": "实况", "decisions": "已定", "hard_nodes": "硬节点", "checks_due": "待核",
              "questions": "待定"},
    "section": {"principle": "总带", "hard": "硬", "preference": "偏好", "goal": "目标", "rule": "规矩", "fact": "实况",
                "hard_node": "硬节点", "decision": "已定", "check": "待核", "question": "待定"},
    "join": "、",
    "sep": "｜",
    "colon": "：",
    "relay": "（{speaker} 转述）",
    "reason": "理由：{reason}",
    "since": "自 {when}",
    "due": "到期 {when}",
    "overdue": "已逾期",
    "latest": "最晚 {clock} 出发",
    "latest_unknown": "最晚出发没核",
    "now": "现在 {time}（{zone}，{day} {weekday}）",
    "weekdays": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "deadline": {"external": "外部截止", "plan": "计划目标（不是外部截止）", "unmarked": "没标是外部截止还是计划目标"},
    "margin": "计划留 {minutes} 分钟余量",
    "checked_by": "核过：{source}，{when}",
    "stored_only": "没核过：计划 V{version} 存的（{when} 起）",
    "stored_unknown": "没核过：存进计划的时刻不明",
    "may_have_changed": "超过 {hours} 小时，可能已变",
    "locked": "锁定 {paths}",
    "adjustable": "涉及 {paths}（可调整）",
    "default": "默认：{text}",
    "memory_pending": "待确认",
    "readings": "读法：{readings}",
    "options": "可选：{options}",
    "todo": {"possible_contradiction": "宿主：问用户两条都算还是只算一条；没答前照默认，不替用户挑读法",
             "memory_pending": "宿主：在对话里问用户这几条这趟还用不用，按回答逐条记“沿用”或“这趟不用”",
             "undecided": "要用户定；宿主：问一次，没答前照默认",
             "undecided_no_default": "要用户定；宿主：问一次，没答前不替用户选"},
    "unknown_time": "时刻未知",
    "unknown_speaker": "说话人未知",
    "unknown_nature": "来源性质未知",
    "order_none": "次序：{ids} 之间没有用户定的次序 → 冲突时给方案",
    "order_user": "次序：{ids}｜用户定｜原话 {event}",
    "order_word": " 先于 ",
    "omitted_line": "没放进来：{count} 条",
    "details": "细节：",
    "candidate": "候选：草稿 {draft}（不计预算，按草稿号取）",
    "full_quotes": "原句全文：",
    "verbatim": "逐字",
    "restated": "转述",
    "nature": {"user_verbatim": "逐字", "host_restatement": "宿主转述", "screenshot_extraction": "截图转写",
               "agent_inference": "推断"},
    "dispositions": "逐条处置：每条写 用了（带块编号）、不适用（写理由）或权衡掉（写理由）",
    "dispositions_orders": "；没有用户定次序的两条偏好不写权衡掉，分进不同方案",
    "degraded": "{section}·只列编号（原文按编号取）：{ids}",
    "unknown": "适用集没定：{group}｜{reason}",
    "partial": "只列了编号：{groups}（原文按编号取）",
    "over": "硬约束过多：约 {tokens} token，预算 {budget}",
    "over_fixed": "固定部分超预算：约 {tokens} token，预算 {budget}；写法、包头与机读部分约 {fixed}",
    "header_title": "# 约束包 {pack_id}｜{title}｜{people}｜{window}｜{place}｜{channel}",
    "header_base": "基于 V{version}",
    "header_commit": "提交 {head}",
    "header_no_commit": "提交 无",
    "header_single": "单库",
    "header_raw": "原话到 {when}",
    "header_no_raw": "还没有原话",
    "header_compiled": "编于 {when}",
    "header_unsynced": "未入仓 {count} 条",
}

EN = {
    "strength": {"must": "must", "forbid": "forbidden", "prefer": "preferred", "optional": "optional"},
    "basis": {"user_explicit": "user said", "direction_set": "user set the direction", "recommended_default": "recommended default",
              "agent_inference": "inferred"},
    "decided_by": {"user": "user decided", "agent": "agent decided"},
    "scenario": {"quick": "quick question", "status": "status report", "replan": "replan, first reply", "plan": "plan from scratch",
                 "compare": "comparison", "requirements": "requirements", "system": "system question"},
    "wide": "deep check",
    "channel": {"*": "all channels", "conversation": "conversation", "site": "site", "plan": "plan"},
    "all_places": "all places",
    "all_day": "all day",
    "omitted": {"out_of_scope:people": "other people", "out_of_scope:time": "outside the hours", "out_of_scope:date": "other dates",
                "out_of_scope:place": "other places", "out_of_scope:channel": "other channels", "over_budget": "over budget"},
    "group": {"items": "items", "facts": "facts", "decisions": "decisions", "hard_nodes": "hard nodes", "checks_due": "checks",
              "questions": "open questions"},
    "section": {"principle": "Always", "hard": "Hard", "preference": "Preference", "goal": "Goal", "rule": "Rule", "fact": "Fact",
                "hard_node": "Hard node", "decision": "Decided", "check": "Check", "question": "Open"},
    "join": ", ",
    "sep": " | ",
    "colon": ": ",
    "relay": " (relayed by {speaker})",
    "reason": "reason: {reason}",
    "since": "since {when}",
    "due": "due {when}",
    "overdue": "overdue",
    "latest": "leave by {clock}",
    "latest_unknown": "latest departure not checked",
    "now": "Now {time} ({zone}, {weekday} {day})",
    "weekdays": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "deadline": {"external": "external deadline", "plan": "plan target (not an external deadline)",
                 "unmarked": "not marked as external deadline or plan target"},
    "margin": "plan keeps {minutes} min margin",
    "checked_by": "checked: {source}, {when}",
    "stored_only": "not checked: stored in plan V{version} (since {when})",
    "stored_unknown": "not checked: when the plan stored it is unknown",
    "may_have_changed": "older than {hours} h, may have changed",
    "locked": "locked {paths}",
    "adjustable": "covers {paths} (adjustable)",
    "default": "default: {text}",
    "memory_pending": "awaiting confirmation",
    "readings": "readings: {readings}",
    "options": "options: {options}",
    "todo": {"possible_contradiction": "host: ask the user whether both hold or only one; until then keep the default, "
                                       "never pick a reading for the user",
             "memory_pending": "host: ask the user in the conversation whether these still apply on this trip and record "
                               "each answer as kept or not this trip",
             "undecided": "the user decides; host: ask once, keep the default until then",
             "undecided_no_default": "the user decides; host: ask once, never choose for the user"},
    "unknown_time": "time unknown",
    "unknown_speaker": "speaker unknown",
    "unknown_nature": "nature unknown",
    "order_none": "Order: no user order between {ids} → give options when they compete",
    "order_user": "Order: {ids} | user decided | message {event}",
    "order_word": " before ",
    "omitted_line": "Not included: {count}",
    "details": "Details: ",
    "candidate": "Candidate: draft {draft} (outside the budget; read it by draft ID)",
    "full_quotes": "Full messages:",
    "verbatim": "verbatim",
    "restated": "restated",
    "nature": {"user_verbatim": "verbatim", "host_restatement": "host restatement", "screenshot_extraction": "from screenshot",
               "agent_inference": "inferred"},
    "dispositions": "Disposition of each item: write used (with block IDs), not applicable (with reason) or traded off (with reason)",
    "dispositions_orders": "; two user preferences without a user order are never traded off: put them in different options",
    "degraded": "{section}, IDs only (read the text by ID): {ids}",
    "unknown": "Applicable set not settled: {group} | {reason}",
    "partial": "IDs only: {groups} (read the text by ID)",
    "over": "Too many hard conditions: about {tokens} tokens, budget {budget}",
    "over_fixed": "Fixed part over budget: about {tokens} tokens, budget {budget}; writing rules, header and metadata about {fixed}",
    "header_title": "# Pack {pack_id} | {title} | {people} | {window} | {place} | {channel}",
    "header_base": "based on V{version}",
    "header_commit": "commit {head}",
    "header_no_commit": "no commit",
    "header_single": "single store",
    "header_raw": "messages to {when}",
    "header_no_raw": "no messages yet",
    "header_compiled": "compiled {when}",
    "header_unsynced": "{count} not in the repo yet",
}

WORDS = {"zh": ZH, "en": EN}


def words(language: str) -> dict:
    return WORDS["en" if language == "en" else "zh"]


def local(stamp: str, zone: str) -> datetime:
    return datetime.fromisoformat(stamp).astimezone(ZoneInfo(zone))


def abbreviation(zone: str, day: str, clock: str | None = None) -> str:
    """The zone's abbreviation on that local day (CEST in July, CET in November); noon avoids the switch hour."""
    moment = datetime.fromisoformat(f"{day}T{clock or '12:00'}").replace(tzinfo=ZoneInfo(zone))
    return moment.tzname() or zone


def stamp_clock(stamp: str, zone: str) -> str:
    return local(stamp, zone).strftime("%H:%M")


def stamp_day_clock(stamp: str, zone: str) -> str:
    return local(stamp, zone).strftime("%m-%d %H:%M")


def stamp_with_zone(stamp: str, zone: str, *, with_day: bool = True) -> str:
    moment = local(stamp, zone)
    return f"{moment.strftime('%m-%d %H:%M' if with_day else '%H:%M')} {moment.tzname() or zone}"


def short_day(day: str) -> str:
    return day[5:]
