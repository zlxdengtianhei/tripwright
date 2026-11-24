from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from tripwright import agents, consider, packs
from tripwright import locks as lock_paths
from tripwright.flow import FULL, PLANNING, SHORT_ROAD, Flow
from tripwright.checks import check_plan, departure_clock
from tripwright.events import Events
from tripwright.jobs import Jobs
from tripwright.ledger import Carry, Changes, Fact, Item, Ledger, PendingCheck, Proposal, Relation
from tripwright.query import QueryRouter, checked_arguments, lookup_failed
from tripwright import catalog, decide, recall, standing
from tripwright.render import first_part
from tripwright.render.opening import material as opening_material
from tripwright.repo import files as repo_files, memory, notices, replay
from tripwright.repo.outbox import Outbox
from tripwright.storage import Store, clock, encode, identifier, now
from tripwright.trip import Trip, aware, input_problems, json_type, local_instant
from tripwright.usage import checked_usages
from tripwright.versions import Versions, commit_key, ledger_only, proposal_key

# What a turn may record from the message it receives (CONTRACTS 1.3): first-hand facts, items, their relations and the
# answer about imported long-term entries. Decisions lock plan parts and go through propose and commit.
TURN_RECORDS = ("items", "facts", "relate", "carry", "pending_checks")
SKELETON = {"quick": "quick", "status": "quick"}
FRAME_MINUTES = packs.STEP_MINUTES  # S4h-a: a quick latest question's frame holds for lookups this long after its turn
# D-20: a second prohibition under the key of one in force; both hold, and the host sees the old one to replace it if meant
ALONGSIDE = dict(zh="同一个 key 下已有一条禁止在生效：“{old}”（{alongside}）。这一条也记下了，两条都生效。如果这一条是要替换或放宽旧的那条，"
                    "重记这一条并在 exclusive_with 里写 {alongside}，服务会问用户留哪一条。",
                 en="A prohibition under the same key is already in force: “{old}” ({alongside}). This one is recorded too; both "
                    "hold. If this one is meant to replace or narrow the old one, record it again with exclusive_with naming "
                    "{alongside}, and the service asks the user which one stays.")
FIRST_TITLES = dict(zh="按存的计划先说（时刻还没核）", en="From the stored plan first (times not checked yet)")
SECOND_TITLES = dict(zh="核过之后", en="After checking")
# Every message to the user is the answer itself, never a note about what the host is doing (baseline 2026-11-19: the first
# message of one run read “I'll start by recording your message.”).
WORK_NOTES = dict(
    zh="发给用户的每条消息都是答话本身，不写“我先记录消息”“我这就去查”这类工作注记。",
    en="Every message to the user is the answer itself, never a work note.")
# A road question the service cannot draft a first part for (baseline 2026-11-19: 13 of 15 real questions had none, and
# the first text came 4 to 19 minutes later; the model's own short messages then gave nothing to act on in 5 of 10, and one
# read a stored “last service 23:53” as the conclusion, which was wrong). So the short message must carry one thing to act
# on, plan times and services go as archived and unchecked, and the second part is its own message (the user, 2026-10-29:
# “你先给我终端输出我现在的走法”).
SEPARATE_PARTS = dict(zh="第二段另发一条，两段标题不并进一条。", en="The second part is its own message.")
FIRST_ARCHIVED = dict(
    zh="第一段里的存档时刻与车次（末班、最晚、硬节点）一律写“存档写 X，还没核”，不当结论；问最晚、末班时第一段只给存档值加“没核”，"
       "结论放第二段。",
    en="Stored times and services in the first part go as “the plan stores X, not checked yet”, never as a conclusion; for "
       "a latest question the first part gives only the stored value.")
ROAD_OPENING = dict(
    zh="先不调任何工具，用 `opening_material` 和常识写一两句能照着走的话发出去（时刻写“存档写 X，还没核”），再去查。",
    en="First call no tool: write one or two lines you can act on from `opening_material` and common sense and send them "
       "(times go as “the plan stores X, not checked yet”), then check.")
# Answer before saving (the user, 2026-10-29: “你先给我终端输出我现在的走法，然后再去更新网站”; baseline 2026-11-19: 8 of 15
# saves came before the first text). The order of the messages and calls, not a claim that a save already happened.
SAY_BEFORE_SAVE = dict(
    zh="先说后存：先在对话里把走法或答案给用户，再 propose/commit；存和更新页面都在回答之后。",
    en="Answer first: give the walk or the answer in the conversation, then propose/commit; the save and page follow.")
MEMORY_ASK = dict(zh="长期条目待确认：在对话里问用户这趟还用不用；按回答调 turn(record={carry:[{id,status}]})。",
                  en="Long-term entries need confirmation: ask whether they still apply to this trip; record the answer with "
                     "turn(record={carry:[{id,status}]}).")


def message_language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


# Stored plans and quotes keep the language they were written in, and a host answers in that language unless told: with
# a Chinese trip GPT answered an English question in Chinese in 3 of 5 runs, and in 0 of 5 with this sentence first in
# `next` (2026-11-11). The opposite direction needs no sentence: on an English trip GLM, Kimi and DeepSeek answered
# Chinese questions in Chinese in all 26 conversations (2026-11-11).
REPLY_IN_ENGLISH = "Reply in English: translate the stored text you use; keep place names as written."


# Reply flow "two" (TRIPWRIGHT_REPLY_FLOW): the one sentence a host follows reliably is "send this and end the reply"
# with the first part's own closing line; anything about the second part in the same field makes it go on (replays
# of 2026-11-11). What comes after waits in after_first_part.
SEND_AND_END = dict(
    zh="先把 first_part.text 原样发出，作为这一条回复的第一条、也是唯一一条消息，末行是 first_part.later，然后结束这一条；"
       "发之前不调别的工具、不写别的字。第二段另发一条，两段标题不并进一条。" + WORK_NOTES["zh"] +
       "能在后台派 researcher 的：唯一可以先做的是在后台派它去查，然后照样原样发出 first_part.text（不加 later 那一行），"
       "不另写“正在查”这类话；它的报告回来后写第二段。",
    en="Send first_part.text as it is as this reply's first and only message, its last line first_part.later, then end the "
       "reply; call no other tool and write nothing else first. The second part is its own message; both headings never in one. "
       + WORK_NOTES["en"] +
       " A background researcher, if you can dispatch one, goes first; then send first_part.text as it is all the same and "
       "add no “still checking” sentence; its report starts the second part.")
# A first part quotes stored text as written. On a trip stored in Chinese an English one came out half Chinese
# (2026-11-11), so the host translates that one; every other first part is still sent as it is.
SEND_IN_ENGLISH = SEND_AND_END["en"].replace("as it is all the same", "that way all the same").replace(
    "as it is", "in English (translate its stored text; keep names, times and numbers as written)")
# A plan draft that brings in unverified facts waits for the user's answer (versions.unanswered). propose says so
# before any commit is tried, and the turn that carries the answer says what to do with the draft.
ASK_USER = dict(zh="这次改动新带来 {n} 条没核的事实，commit 要等用户回话。先告诉用户：改动还没存、哪几条没核、各自的后果与备选；"
                   "再照 options 一问：每个选项一句后果，标出推荐的，说不回按哪个。"
                   "用户回话后先调 turn；同意就 commit(draft_id, accept_unverified=true)。",
                en="This change brings in {n} unverified fact(s); commit waits for the user's answer. Tell the user that the change "
                   "is not saved yet, which facts are unknown, what follows from each and the alternative; then ask once with "
                   "options: one consequence per option, the recommended one marked, and what happens without an answer. After "
                   "the answer call turn; if the user agrees, commit(draft_id, accept_unverified=true).")
# What the user can answer to a change with unverified facts (2026-11-13: "每次询问时尽量带着可选项的结果"): at most
# three whole options, each with its consequence, one recommended, one taken without an answer. Cautious: nothing is
# saved without an answer, so that one keeps the plan. Proactive: the change is saved; the options are to keep or undo.
SAVE_OPTIONS = dict(
    zh=dict(ask=[("save", "照这样存，到时自己看一眼没核的那 {n} 条", "计划现在就更新，没核的在计划和页面上标着"),
                 ("check_first", "我先查这几条再存", "多等一轮；查不到的仍标没核"),
                 ("keep", "不改，保持现在的计划", "这次改动不存，计划照旧")],
            tell=[("keep", "保留这版", "已存；没核的在计划和页面上标着"),
                  ("undo", "撤回", "计划回到这次改动之前")]),
    en=dict(ask=[("save", "Save it as it is and check the {n} unverified fact(s) yourself before going", "The plan changes now; "
                  "the unverified facts are marked in the plan and on the page"),
                 ("check_first", "Let me look them up first, then save", "One more round; what cannot be checked stays marked"),
                 ("keep", "Leave the plan as it is", "This change is not saved")],
            tell=[("keep", "Keep this version", "Saved; the unverified facts are marked in the plan and on the page"),
                  ("undo", "Undo", "The plan goes back to before this change")]))


def save_options(asks: bool, n: int, base: int, language: str) -> list[dict]:
    """The options for a change with unverified facts: recommended `save` (cautious) or `keep` (proactive); taken
    without an answer: `keep` in both, which in the cautious mode means nothing is saved."""
    rows = SAVE_OPTIONS["en" if language == "en" else "zh"]["ask" if asks else "tell"]
    recommended = "save" if asks else "keep"
    return [dict(id=key, say=say.format(n=n), then=then.format(base=base), **({"recommended": True} if key == recommended else {}),
                 **({"if_no_answer": True} if key == "keep" else {})) for key, say, then in rows]
# In the proactive save mode (the user's save_mode setting, or the default without one) nothing waits for the
# user: propose names the same facts as tell_user, and the answer says how to get the previous version back.
TELL_USER = dict(zh="这次改动新带来 {n} 条没核的事实；现在是主动模式，不等回话：commit(draft_id, accept_unverified=true) 存下，"
                    "回答里说改了什么、哪几条没核、各自的后果与备选，末尾原样带上 commit 返回的 tell_user.say（用户回“撤回”时，服务在 turn 里直接回到上一版）。",
                 en="This change brings in {n} unverified fact(s); the save mode is proactive, so nothing waits for the user: "
                    "commit(draft_id, accept_unverified=true), then tell the user what changed, which facts are unknown, what "
                    "follows from each and the alternative, and end with the tell_user.say commit returns, as it is (on “undo”, "
                    "the service goes back to the previous version in turn).")
# The sentence a proactive answer ends with (2026-11-13: GPT-6 Luna saved in the same turn 2 times in 2 and named the
# unverified fact, but said nothing about undoing it).
# No version number (D-22, 2026-11-14): before the commit the number is not known for certain (a host that commits the
# message's requirements first makes another version; GLM said “我恢复到第 5 版” when the undo went back to 3). The exact
# number is only in the commit receipt's tell_user.say.
UNDO_SAY = dict(zh="不想要这次改动，回“撤回”，我恢复上一版。", en="If you don't want this change, reply “undo” and I'll restore the previous version.")
# The sentence every plan change saved in the proactive mode ends with (the user, 2026-11-14: "第一轮回复完后可以提示：
# 当前在主动模式，现在开始更新页面；如果想撤回可以直接回复'撤回'"). commit returns it, so it does not rest on the model
# remembering it; `page` when a public page is connected (it is rebuilt in the background), `plain` otherwise.
PROACTIVE_SAY = dict(zh=dict(page="当前是主动模式：改动已存，现在开始更新页面；想撤回，直接回复“撤回”。",
                             plain="当前是主动模式：改动已存；想撤回，直接回复“撤回”。"),
                     en=dict(page="You're in proactive mode: the change is saved and the page is being updated now; to undo it, just reply “undo”.",
                             plain="You're in proactive mode: the change is saved; to undo it, just reply “undo”."))
# After an undo: the plan is back, and the first time this user undoes a change, the cautious mode is offered once
# (the user, 2026-11-14: "用户回复撤回后，再告诉他我们有谨慎模式可选，建议他考虑谨慎模式"; once, not on every undo).
RESTORED_SAY = dict(zh=dict(page="已撤回：计划回到第 {restored} 版，页面也在更新。", plain="已撤回：计划回到第 {restored} 版。",
                            taken="随撤回一起撤掉：{what}。", join="；",
                            keep="其中哪条要留，再说一次就行。",
                            tickets="这些票和预订也随撤回撤了：{what}；票本身要你自己去退或留。",
                            still="你之前定的「{what}」仍然有效，和现在的计划不一致；要不要改，你说。",
                            offer="另外我们有谨慎模式：改计划前先把改动给你看，你同意了再存。建议考虑；想用就回“以后先问我”。"),
                    en=dict(page="Undone: the plan is back to version {restored}, and the page is being updated.",
                            plain="Undone: the plan is back to version {restored}.",
                            taken="Taken back with it: {what}.", join="; ",
                            keep="If you want to keep one of them, say it again.",
                            tickets="These bookings went with the undo: {what}; the tickets themselves are yours to cancel or keep.",
                            still="What you decided earlier, “{what}”, still stands and does not match the plan now; tell me if you want it changed.",
                            offer="We also have a cautious mode: I show you a change first and save it only after you agree. "
                                  "Worth considering; to switch, reply “ask me first from now on”."))
SAY_HOW = dict(zh="回答末尾原样带上 say。", en="End the answer with say as it is.")
# A message that only takes the last change back (“撤回”, “undo”): the service restores it in the same turn, so the
# agent neither works out the version nor calls propose and commit (round 4, 2.1).
UNDO_WORDS = re.compile(r"^\s*(?:撤回|撤销|改回去|恢复上一版|不要这次改动|undo|revert|change it back|undo (?:that|it|the change))"
                        r"[\s。．.!！~～吧了啊呀，,]*$", re.IGNORECASE)
UNDO_NEXT = dict(zh=dict(taken="撤回已经做好了（服务在 turn 里回到第 {number} 版，那几轮记下的条目随撤回撤掉）：不要再调 propose 或 commit，"
                              "照 undo.say 原样告诉用户，说回到第几版、撤掉了哪几条；用户说哪条要留，再记一次。",
                         plain="撤回已经做好了（服务在 turn 里回到第 {number} 版，没有条目要撤）：不要再调 propose 或 commit，"
                               "照 undo.say 原样告诉用户，说回到第几版。"),
                 en=dict(taken="The undo is done (the service went back to version {number} in this turn, and the entries those turns "
                              "recorded went with it): do not call propose or commit; tell the user as undo.say says, naming the version "
                              "and what was taken back. When the user says which to keep, record it again.",
                         plain="The undo is done (the service went back to version {number} in this turn; no entries were taken back): "
                               "do not call propose or commit; tell the user as undo.say says, naming the version."))
# The reminder a turn that is itself an undo carries: the service has done the work, so the planning line (which asks for a
# record, a verifier and a commit) would contradict this response's `next` (round 4 review).
UNDO_LINE = dict(zh="撤回：服务已经在 turn 里做完，照 undo.say 原样回答，不要再 propose 或 commit。",
                 en="Undo: the service did it in this turn; answer as undo.say says and do not propose or commit again.")
# What the main agent does once the verifier's report is recorded (analysis 5.14): by verdict, and for an incomplete
# check by save mode. The cautious question comes with options, as ask_user's do.
REPORT_NEXT = dict(
    zh=dict(passed="核对员判通过：可以 commit。",
            conflict="核对员判冲突：这份草稿存不下。照它说的冲突改草稿、重新 propose，再派核对员。",
            ask="核对员有没核到的：先问用户，至多三个选项，每个一句后果：① 照这样存，没核到的到时自己看一眼（推荐）；② 我先补核再存；③ 不改（不回话就按这个）。"
                "用户回话后 commit(accept_unverified=true)。",
            tell="核对员有没核到的：现在是主动模式，commit(accept_unverified=true) 存下，回答里逐条说没核到的和后果，末尾说回“撤回”能恢复上一版。",
            incomplete="核对员判不完整，但没列出没核到的：可以 commit。"),
    en=dict(passed="The verifier passed the draft: commit.",
            conflict="The verifier found a conflict: this draft cannot be saved. Change the draft as it says, propose again and dispatch the verifier again.",
            ask="The verifier could not check everything: ask the user first, with at most three options, each with its consequence: "
                "1 save as it is and look at the unchecked items on the spot (recommended); 2 check them first, then save; "
                "3 no change (taken without an answer). After the answer, commit(accept_unverified=true).",
            tell="The verifier could not check everything; the save mode is proactive: commit(accept_unverified=true), say each unchecked "
                 "item and what follows from it, and end with how to undo the change.",
            incomplete="The verifier found the draft incomplete but listed nothing unchecked: commit."))


def verifier_report(report) -> dict:
    """The shape result(report=...) accepts: a verdict, one sentence per item it could not check, one per conflict."""
    if not isinstance(report, dict):
        raise ValueError("report is an object {verdict, unchecked, conflicts}")
    verdict = report.get("verdict")
    if verdict not in ("passed", "conflict", "incomplete"):
        raise ValueError(f"report.verdict is passed, conflict or incomplete, got {verdict!r}")
    lines = {}
    for key in ("unchecked", "conflicts"):
        value = report.get(key) or []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(line, str) and line.strip() for line in value):
            raise ValueError(f"report.{key} is a list of sentences, one per item")
        lines[key] = [line.strip() for line in value]
    if verdict == "conflict" and not lines["conflicts"]:
        raise ValueError("report.conflicts names each conflict in one sentence when the verdict is conflict")
    unknown = set(report) - {"verdict", "unchecked", "conflicts"}
    if unknown:
        raise ValueError(f"report takes verdict, unchecked and conflicts; unknown: {', '.join(sorted(unknown))}")
    return dict(verdict=verdict, **lines)


# How the user switches the save mode (consider.save_mode), returned with every turn.
SAVE_MODE_HOW = dict(zh="用户说“以后直接改，不用问我”或“以后先问我”这类话时，在这条 turn 的 record 里记一条 setting：key=save_mode，"
                        "value 写 proactive（主动）或 cautious（谨慎），long_term=true（写在条目上），turn 带 speaker。只记用户自己说的，不替用户选。",
                     en="When the user says “from now on just change it” or “ask me first from now on”, record a setting in this "
                        "turn's record: key=save_mode, value proactive or cautious, long_term=true on the item, with the turn's speaker. Record only "
                        "what the user said; never choose for the user.")
# A refused draft goes back to the user anyway: the unverified facts its change brings in are said in the same reply,
# so the corrected draft does not need one more round for them (2026-11-12, Fable: a draft refused for a conflict, the
# user chose an option, and the facts had never been pointed out).
ASK_WITH_REFUSAL = dict(zh="草稿没被接受（见 notice、checks.conflicts）。这次改动还新带来 {n} 条没核的事实：回头问用户怎样改时，一并说明哪几条没核、"
                           "各自的后果与备选。用户回话后先调 turn；改好的草稿在用户同意后 commit(draft_id, accept_unverified=true)。",
                        en="The draft was not accepted (see notice and checks.conflicts). Its change also brings in {n} unverified "
                           "fact(s): when you ask the user how to change it, say which facts are unknown, what follows from each and "
                           "the alternative. After the answer call turn; commit the corrected draft with accept_unverified=true if "
                           "the user agrees.")
WAITING = dict(zh="草稿 {draft} 在等用户这句回话（{n} 条没核的事实）。用户同意照这样存：commit(draft_id='{draft}', accept_unverified=true)，"
                  "不用重新 propose；用户要改：按新说的重新 propose。",
               en="Draft {draft} was waiting for this reply ({n} unverified fact(s)). If the user agrees to save it as it is: "
                  "commit(draft_id='{draft}', accept_unverified=true), no new propose; if the user changes something: propose again.")
WAITING_STALE = dict(zh="草稿 {draft} 在等用户这句回话（{n} 条没核的事实），但账本或计划在那之后变了。用户同意照这样存：照同样的改动重新 propose，"
                        "再 commit(accept_unverified=true)；用户答过的事实不再问。",
                     en="Draft {draft} was waiting for this reply ({n} unverified fact(s)), but the ledger or plan changed since. If the "
                        "user agrees to save it as it is: propose the same change again, then commit(accept_unverified=true); "
                        "facts the user has answered are not asked again.")
# trip60: the one instruction before the first message. Nothing else in it invites a lookup or a state read first
# (replays of 2026-11-22: with “state(view='memory') 可看” and “按坐标或站名查” in the same field, hosts read the plan,
# the memory and the files for 1 to 6 minutes before writing anything).
OPEN_FIRST = dict(
    zh="先写第一条消息，再调别的工具：用一到五行直接答这句话里最急的那件事（怎么走、去哪、选哪个、要不要赶），要能照着走；"
       "材料用 first_part、opening_material、consider 里的偏好和常识；存档里的时刻写“存档写 X，还没核”，存档里没有的写“没核”。"
       "两三个选项时标出推荐的。这一条只放第一段；发出去以后接着查、再存，然后另发第二段（标题用 second_part_title，先说和第一段的不同），"
       "不用等用户回话。" + SEPARATE_PARTS["zh"] + WORK_NOTES["zh"],
    en="Write the first message before any other tool: one to five lines you can act on that answer the most urgent thing "
       "this message asks (how to go, where, which one, whether to hurry), from first_part, opening_material, the "
       "preferences in consider and common sense; stored times go as “the plan stores X, not checked yet”, anything not "
       "stored as not checked. With two or three options, mark the recommended one. That message is the first part only; "
       "after it is sent, go on: check, save, then send the second part (heading second_part_title, what differs first) "
       "without waiting for the user. " + SEPARATE_PARTS["en"] + " " + WORK_NOTES["en"])
RECALL_LIMIT = 6  # entries a turn's `recall` lists
RECALL_TEXT = 140  # characters of each entry's text in a turn
# After query(kind="conflict") (decide.py): what the host does with the question, and with the next message.
DECIDE_HOW = dict(
    zh="把 say 原样放进这次回答（第二段开头），只问这一问；计划先不改。用户下一句话就是回答：照他选的那项 propose；没选就照默认，不再问。"
       "同一时段只开一问：这一问回来 queued=true 时先别问，turn 会在那一问答完后把它放到 ask 里。",
    en="Put `say` into this answer as it is (at the start of the second part) and ask only this; do not change the plan "
       "yet. The user's next message is the answer: propose the option they chose, or keep the default if they chose none; "
       "do not ask again. One question is open at a time: when this one comes back queued=true, do not ask it; turn puts "
       "it in ask after the open one is answered.")
DECIDED_LEAD = dict(
    zh="这句话是在回之前那一问（decided）：照用户选的那项改计划（propose），回答里说清选的是哪项；这句话没选就照默认，不再问。",
    en="This message answers the earlier question (decided): change the plan to the option the user chose (propose) and "
       "say which one; if the message chooses none, keep the default and do not ask again.")
# One question is open at a time (decide.py): a second conflict waited; this answer asks it, once.
ASK_OPEN = dict(
    zh="另有一问刚才在排队，这一问现在问（ask 里的 {id}）：把它的 say 原样放进这次回答，只问这一问；下一句再算它的回答。",
    en="One question was waiting its turn; ask it now (ask: {id}): put its say into this answer as it is and ask only "
       "this one; the next message is its answer.")
QUESTION_WAITS = dict(
    zh="还有一问没回（{id}）：这一问先别问，把它的 say 和选项留着；等那一问定了，turn 会把它放到 ask 里，那时再问。",
    en="A question is still unanswered ({id}): do not ask this one yet; keep its say and options. Once that one is "
       "settled, turn puts this one in ask; ask it then.")
# A finding that contradicts the decided plan: how to hand it to the user (decide.py).
CONFLICT_USE = dict(
    zh="查到或用户说的事和已定的计划、预订、决定对不上：不自己改计划，调一次 query(kind='conflict')（参数见工具说明），把它给的 say 问用户一次。",
    en="When something found or said contradicts the decided plan, a booking or a decision: do not change the plan yourself; "
       "call query(kind='conflict') once (arguments in the tool description) and ask its `say` once.")
AFTER_OPEN = dict(zh="第一条发出去以后照这里做：", en="After the first message is sent: ")
# Q19 (2026-11-23): the user's repository is the database. `recall` lists what it already holds for this message;
# what it lacks is looked up, and what is looked up goes back into it.
# 2026-11-24 (route F, the catalog): `recall.found` is still the lexical six; `recall.catalog` lists every entry as one row so the
# host picks by meaning what the words missed, and fetches those with `ids`. The text is the host's instruction for it: read the
# catalog from the top, take only what this message needs (none is allowed: a message that needs nothing from the repository
# must not pull entries in), fetch before the second message, an expired row says where to look again. Two sentences beyond the
# designed text say what `+n` and `omitted` are and how `list` opens them (without them a merged row's other members, and the
# entries the budget left out, cannot be reached by id).
RECALL_USE = dict(
    zh="recall.found 是字面最像的几条全文（opening_material 里“仓里存的”是其中最好的）：没过期的直接用、写出处，不重查。"
       "recall.catalog 是仓里现有条目的目录，一行一条：id、类别、开头几十个字（短的整条都在；行尾 +n 是开头相同、并在这一行的条数）。"
       "写回答前把 catalog 从头看一遍，只挑回答这句话真用得上的——票的规则、订了什么、用户以前说过的、以前查过的事——"
       "即使和用户的说法没有一个词相同；用 query(kind='saved', arguments={ids:[…]}) 一次取回全文和出处，最多 8 条；用不上就一条也不取。"
       "着急的第一段先用 found 和 catalog 里已经写全的行，取回放在第二段之前。标“过期”的行不能直接用，但说明以前查过、去哪重查。"
       "catalog.omitted 是装不下、没列出的条数：看哪一组就 query(kind='saved', arguments={list: 那一组的 list 值})，行末带 +n 的行要看并在一起的几条，list 用它的 id。"
       "catalog 里没有的，就说仓里没存，再去查；用词找用 query(kind='saved', arguments={text: 用户的词加当地语言词})——地名只写中文常对不上仓里的当地拼法。"
       "网上读到、写进回答的事实，一页一次 query(kind='web_evidence') 记进仓。",
    en="recall.found is the few entries that match the words best, in full (the “In the repository” lines of opening_material are the best "
       "of them): use what has not expired, with its source, without looking again. recall.catalog lists everything the repository holds, "
       "one row each: id, kind, the first few dozen characters (a short entry is whole; a trailing +n counts the entries with the same "
       "beginning merged into the row). Before writing the answer read the catalog from the top and pick only what this message really needs "
       "— the ticket rules, what is booked, what the user said before, what was looked up before — even when it shares no word with what "
       "the user wrote; fetch them with one query(kind='saved', arguments={ids:[…]}) for the full text and source, at most 8; fetch none "
       "when none is needed. In an urgent first message use found and the catalog rows that are already whole; fetch before the second "
       "message. A row marked expired cannot be used as it stands, but it says something was looked up before and where to look again. "
       "catalog.omitted counts what did not fit and is not listed: to see a group, query(kind='saved', arguments={list: that group's list "
       "value}); for a row ending in +n, use its id as list to see the merged entries. What the catalog lacks: say the repository does not "
       "hold it, then look it up; to search by words use query(kind='saved', arguments={text: the user's words and the local-language "
       "words}) — a place named only in Chinese often does not match the local spelling in the repository. A fact read on the web that "
       "goes into the answer is recorded with query(kind='web_evidence'), once per page.")
AFTER_FIRST = dict(zh="用户回了话、或 researcher 的报告回来之后照这里做，不再调 turn：",
                   en="After the user replies or the researcher's report returns, do this without calling turn again: ")
# explain(record_id) (S5): the ledger tables a record ID is looked up in, and the fields each adds to the record
RECORD_TABLES = (("items", "item"), ("facts", "fact"), ("decisions", "decision"), ("pending_checks", "pending_check"),
                 ("undecided", "question"))
RECORD_FIELDS = dict(item=("kind", "key", "strength", "condition", "valid_until", "reason", "always", "long_term"),
                     fact=("key", "value", "true_from", "valid_until"),
                     decision=("decided_by", "must", "reason", "depends_on", "revisit_when", "locked_paths"),
                     pending_check=("due_at", "responsible", "consequence", "alternative", "result"),
                     question=("options", "default", "item_ids"))


class RecordEntry(BaseModel):
    """What placeholder_sources reads of a record entry: an object whose source, when sent, is an object too. The rest is
    checked with the ledger's models after the placeholder is filled in."""
    model_config = ConfigDict(extra="allow")
    source: dict[str, Any] = Field(default_factory=dict)


RecordShape = create_model("RecordShape", __config__=ConfigDict(extra="allow"), **{part: (list[RecordEntry], []) for part in TURN_RECORDS})
RECORD_MODELS = dict(items=Item, facts=Fact, pending_checks=PendingCheck, relate=Relation, carry=Carry)
# The keys a wrong record entry is told it needs, e.g. `record.facts.0 must be an object {key, value, …}, got array`.
RECORD_SHAPES = {f"record.{part}.*": "{" + ", ".join(name for name, field in model.model_fields.items() if field.is_required()) + "}"
                 for part, model in RECORD_MODELS.items()} | {
                 f"record.{part}.*.source": '{event ("this" for this message), optional nature and basis}' for part in TURN_RECORDS}


def checked_record(record) -> dict:
    """The shape of turn.record, checked before anything reads it (B3g): the record and each of its parts' entries and
    their sources must be objects in arrays. A wrong one is refused with its field path, what it needs and what was sent
    (the X1a form), before placeholder_sources fills in the message and the ledger's models check the rest."""
    if not isinstance(record, dict):
        raise ValueError(f"Invalid record: record must be an object, got {json_type(record)}")
    try:
        RecordShape.model_validate(record)
    except ValidationError as error:
        raise ValueError("Invalid record: " + "; ".join(input_problems(error, "record", RECORD_SHAPES))) from None
    return encode_copy(record)


def placeholder_sources(record: dict, event_id: str) -> dict:
    """Records sent with a turn name their message as "this" (its ID does not exist yet). `record` has passed
    checked_record."""
    record = encode_copy(record)
    for part in TURN_RECORDS:
        for entry in record.get(part, []):
            source = entry.setdefault("source", {})
            if source.get("event") in (None, "", "this"):
                source["event"] = event_id
    return record


def person_id(value, plan: dict | None, field: str):
    """Resolve a trip person by ID or display name, without guessing partial matches."""
    if value is None or plan is None:
        return value
    if not isinstance(value, str):
        return value
    if value == "*":
        return value
    people = plan.get("people", [])
    folded = value.strip().casefold()
    matches = [person for person in people if folded in (person["id"].casefold(), person["name"].casefold())]
    if len(matches) == 1:
        return matches[0]["id"]
    choices = ", ".join(f"{person['id']} ({person['name']})" for person in people) or "no people in this trip"
    raise ValueError(f"{field}: unknown person {value!r}; this trip's people are {choices}")


def person_list(value, plan: dict | None, field: str):
    if not isinstance(value, list) or plan is None:
        return value
    return [person_id(entry, plan, f"{field}.{index}") for index, entry in enumerate(value)]


def normalize_record_people(record, plan: dict | None, root: str):
    if not isinstance(record, dict):
        return record
    result = encode_copy(record)
    for part, entries in result.items():
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and "subject" in entry:
                entry["subject"] = person_list(entry["subject"], plan, f"{root}.{part}.{index}.subject")
            # long_term belongs to the item; GPT-6 Luna put it into source 3 times in 6 runs (2026-11-13), and the whole
            # record was refused for it.
            source = entry.get("source") if isinstance(entry, dict) else None
            if part == "items" and isinstance(source, dict) and "long_term" in source and "long_term" not in entry:
                entry["long_term"] = source.pop("long_term")
    return result


def merged_plan(current: dict | None, days=None, places=None, bookings=None) -> dict:
    """Merge whole-day and by-ID patches, then let the normal Trip and lock checks judge the result."""
    if current is None:
        raise ValueError("Partial plan changes need an existing plan; use state(view='blank') and propose(plan=...)")
    if not all(isinstance(value, list) for value in (days, places, bookings)):
        raise ValueError("days, places and bookings must be arrays")
    result = encode_copy(current)
    for name, values, key in (("days", days, "date"), ("places", places, "id"), ("bookings", bookings, "id")):
        for index, value in enumerate(values):
            if not isinstance(value, dict) or not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"{name}.{index}.{key} is required and must be a string")
            found = next((position for position, entry in enumerate(result[name]) if str(entry[key]) == value[key]), None)
            if found is None:
                result[name].append(encode_copy(value))
            else:
                result[name][found] = encode_copy(value)
    result["days"].sort(key=lambda day: day["date"])
    return result


BLOCK_FIELDS = ("start", "end", "kind", "place", "options", "text", "certainty", "mode", "source", "app", "fare", "booking",
                "note", "transfer_minutes", "walk_minutes", "baggage_holder", "equipment", "critical_facts", "origin",
                "destination", "line")


def merged_blocks(plan: dict, blocks) -> dict:
    """`propose(blocks=[...])` (analysis 5.21, 2026-11-13): change or delete single blocks by ID instead of sending a
    whole day back (WorkBuddy's client failed on propose's long arguments). Each entry is {id, the fields to set (null
    removes one), delete?, date?, plan?}; date and plan (the option's name) only pick among blocks that share an ID.
    The result goes through the same checks as a whole-day patch."""
    if not isinstance(blocks, list):
        raise ValueError("blocks must be an array")
    result = encode_copy(plan)
    for index, entry in enumerate(blocks):
        where = f"blocks.{index}"
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError(f"{where}.id is required: a block ID from state(view='day')")
        unknown = set(entry) - {"id", "delete", "date", "plan", *BLOCK_FIELDS}
        if unknown:
            raise ValueError(f"{where}: unknown fields {', '.join(sorted(unknown))}; a block takes {', '.join(BLOCK_FIELDS)}")
        found = [(day, option, track, block) for day in result["days"] if entry.get("date") in (None, str(day["date"]))
                 for option in day.get("plans", []) if entry.get("plan") in (None, option.get("name"))
                 for track in option.get("tracks", []) for block in track.get("blocks", []) if block["id"] == entry["id"]]
        if not found:
            raise ValueError(f"{where}: no block {entry['id']!r}" + (f" on {entry['date']}" if entry.get("date") else "")
                             + "; state(view='day') lists the block IDs")
        if len(found) > 1:
            places = ", ".join(f"{day['date']} {option.get('name')}" for day, option, _, _ in found)
            raise ValueError(f"{where}: block {entry['id']!r} is in {len(found)} places ({places}); add date and plan to pick one")
        _, _, track, block = found[0]
        if entry.get("delete"):
            if set(entry) & set(BLOCK_FIELDS):
                raise ValueError(f"{where}: delete takes no fields to set")
            track["blocks"].remove(block)
            continue
        for field in BLOCK_FIELDS:
            if field in entry:
                if entry[field] is None:
                    block.pop(field, None)
                else:
                    block[field] = encode_copy(entry[field])
    return result


def checked_plan(plan) -> None:
    """A plan of the wrong shape is refused with one line per field path, what it needs and what was sent (B3f X1a),
    e.g. `plan.days.0.anchors.1.deadline must be 'external' or 'plan', got string`."""
    try:
        Trip.model_validate(plan)
    except ValidationError as error:
        raise ValueError("Invalid plan: " + "; ".join(input_problems(error, "plan"))) from None


def encode_copy(value):
    import json

    return json.loads(encode(value))


def whole_sentences(text: str, limit: int) -> str:
    """At most `limit` characters, cut after the last whole sentence that fits, never inside one."""
    if len(text) <= limit:
        return text
    cut = max(text.rfind(end, 0, limit) for end in ("。", ". ", "；"))
    return text[:cut + 1].rstrip() if cut > 0 else text[:limit]


class Tripwright:
    def __init__(self, store: Store, *, query_router: QueryRouter | None = None, query_timeout: float = 30,
                 deep_timeout: float = 120, repository=None, commit_wait: float = 0.0, language: str = "zh"):
        self.store = store
        self.events = Events(store)
        self.ledger = Ledger(store)
        self.jobs = Jobs(store)
        self.versions = Versions(store, self.jobs)
        self.outbox = Outbox(store)
        self.jobs.handlers["deep_check"] = self.versions.deep_check
        self.jobs.handlers["plan_day"] = self._plan_day
        self.query_router = query_router or QueryRouter(store)
        self.query_router.repository = repository  # `query(kind="saved")` finds entries through the repository's index
        self.query_timeout = query_timeout
        self.deep_timeout = deep_timeout
        # How long commit waits for the Git commit and push (and so for write.state `effective`). 0: it returns once the
        # version is in the index, and the committer commits, pushes and schedules the page in the background (the
        # user, 2026-11-14: the reply must not wait for saving and the site update; they belong to the second half).
        # On a local bare remote the wait was 0.4-2.8 s of every commit, by machine (a fictional trip, 2026-11-14: 2.0-2.8 s on
        # a loaded Mac, 0.44-0.61 s on a Linux server).
        self.commit_wait = commit_wait
        self.language = language
        self.flow = Flow()
        self.versions.requery = self._requery
        self.jobs.async_cleanup = lambda: self.query_router.aclose()
        self.repository = repository
        self.committer = None
        if repository is not None:
            from tripwright.repo.committer import Committer

            self.committer = Committer(self, repository)
            self.committer.attach()

    # --- repository wiring (used by the committer) -------------------------------------

    def bound(self, owner: str):
        if self.repository is not None and owner != self.repository.owner:
            raise ValueError(f"This trip repository is bound to the local identity {self.repository.owner!r}")

    def current_trip(self, owner: str) -> str | None:
        if self.committer is not None:
            return (self.committer.head_record() or {}).get("trip")
        plan = self.versions.current(owner)["plan"]
        return plan["id"] if plan else None

    def trip_folder(self, owner: str) -> str | None:
        from tripwright.repo.layout import trip_folder

        trip = self.current_trip(owner)
        return trip_folder(trip) if trip and self.repository is not None else None

    def repository_files(self, tables, *, trip, upto, repo, at, notices):
        return repo_files.repository_files(self, tables, trip=trip, upto=upto, repo=repo, at=at, notices=notices)

    def replay(self, scratch, owner, op):
        return replay.replay(self, scratch, owner, op)

    def conflict_notice(self, owner, op, reason, canonical):
        return notices.conflict(self.store, owner, op, reason, canonical)

    def open_notices(self, owner):
        return notices.open_notices(self.store, owner)

    def settle_notices(self, owner, op):
        notices.settle(self.store, owner, op)

    def adopt_manual_edits(self):
        from tripwright.repo import manual

        return manual.adopt(self) if self.committer is not None else None

    def take_in(self):
        """Before a write: plan files edited without the tool become a version first (A15), so this write builds on
        them and the commit that follows does not write the previous version's plan over them."""
        if self.committer is not None:
            self.committer.adopt()

    def _has(self, table: str, owner: str, record_id: str) -> bool:
        try:
            self.store.get(table, owner, record_id)
        except ValueError:
            return False
        return True

    def write_ref(self, owner: str, op: dict | None) -> dict:
        return self.outbox.ref(owner, op)

    # --- tools -------------------------------------------------------------------------

    def turn(self, owner: str, text: str, request_id: str | None = None, *, nature: str = "host_restatement",
             native_ref: str | None = None, channel: str = "conversation", subject: list[str] | None = None,
             scope: dict | None = None, scenario: str = "plan", focus_keys: list[str] | None = None,
             exclude_keys: list[str] | None = None, speaker: str | None = None, record: dict | None = None,
             new_trip: str | None = None, points: list | None = None) -> dict:
        self.bound(owner)
        language = message_language(text)
        current_plan = self.versions.current(owner)["plan"]
        speaker = person_id(speaker, current_plan, "speaker")
        subject = person_list(subject, current_plan, "subject")
        record = normalize_record_people(record, current_plan, "record")
        # S5: a record of the wrong shape (or one the ledger refuses, below) never refuses the turn: the message is
        # received and the first part drafted as without it; recorded.not_recorded says what was left out and where to
        # send it again
        refused_record = None
        try:
            record_input = (checked_record(record) if record is not None else None) or None
        except ValueError as error:
            record_input, refused_record = None, str(error)
        # A bad point never refuses the message (trip60): what passes is kept, a place the user's words contradict is
        # replaced by the one they name, and points_note says what changed.
        points, points_note = first_part.lenient_points(points)
        if points:
            points, replaced_places = packs.placed_by_words(current_plan, points)
            points_note += replaced_places
        request_id = request_id or identifier("REQ")
        prior = self.outbox.find(owner, "turn", request_id)
        nature_key = "host_restatement" if nature == "user_verbatim" and not native_ref else nature
        incoming = dict(text=text, nature=nature_key, channel=channel, native_ref=native_ref,
                        speaker=speaker, suspicious_words=[])
        collision = next((event for event in self.store.all("events", owner)
                          if event.get("request_id") == request_id and encode(event["received"]) != encode(incoming)), None)
        if collision is not None or (prior is not None and encode(prior["replay"].get("input")) != encode(record_input)):
            request_id = identifier("REQ")
            prior = None
        self.take_in()
        if new_trip and prior is None:
            memory.prepare_new_trip(self, owner, new_trip)
        with self.store.transaction():
            opened = memory.switch(self, owner, new_trip) if new_trip and prior is None else None
            receipt = self.events.receive(owner, text, request_id, nature=nature, native_ref=native_ref, channel=channel, speaker=speaker)
            event_id = receipt["event_id"]
            recorded = dict(changed=[], not_recorded=[], warnings=[], needs_replan=False)
            if prior is None:
                imported = memory.import_entries(self, owner, opened, new_trip, event_id) if opened else []
                imports = dict(items=[self.store.get("items", owner, entry) for entry in imported if self._has("items", owner, entry)],
                               carry=[self.store.get("carry", owner, entry) for entry in imported]) if opened else None
                changes = None
                if record_input:
                    try:
                        with self.store.attempt():
                            changes, recorded = self._record(owner, placeholder_sources(record_input, event_id), event_id,
                                                            language=language)
                    except ValueError as error:
                        refused_record = str(error)
                # trip60: a standing rule the user says in passing is recorded with the user's words even when the host
                # records none of it (standing.py); the reply says what was recorded, and an undo takes it back.
                own = [item.get("quote") for item in self.store.all("items", owner)
                       if (item.get("source") or {}).get("event") == event_id]
                said = standing.items(text, event_id, recorded_quotes=own)
                if said:
                    try:
                        with self.store.attempt():
                            _, kept = self._record(owner, dict(items=said), event_id, language=language)
                        recorded = dict(recorded, changed=[*recorded["changed"], *kept["changed"]],
                                        standing=[dict(id=item["id"], quote=item["quote"]) for item in self.store.all("items", owner)
                                                  if item["id"] in kept["changed"] and item["key"].startswith(standing.KEY)])
                    except ValueError:
                        pass  # the ledger refused it (e.g. a stable entry): the host's own record path still applies
                if refused_record:
                    recorded = dict(recorded, not_recorded=[*recorded["not_recorded"],
                                                            self._refused_record(record, refused_record, event_id, language=language)])
                ineffective = self._settings_not_in_effect(owner, [item for item in self.store.all("items", owner)
                                                                    if (item.get("source") or {}).get("event") == event_id], language)
                if ineffective:
                    recorded = dict(recorded, not_in_effect=ineffective)
                # What the last turn asked and this message leaves unanswered takes its default (M1, R-58, R-67).
                silent = consider.defaults(self.store, owner, self._asked(owner, event_id), record_input, at=now(), language=language)
                if silent:
                    recorded = dict(recorded, defaulted=silent)
                self.ledger.ensure_profile(owner)
                event = self.store.get("events", owner, event_id)
                op = self.outbox.add(owner, kind="turn", tool="turn", key=request_id, event=event_id, records=recorded["changed"],
                                     replay=dict(event=event, input=record_input, changes=changes, new_trip=new_trip, imports=imports),
                                     state="received" if self.committer else "effective", recorded=recorded)
            else:
                op = prior
                recorded = prior.get("recorded", recorded)
            context_id = identifier("CTX")
            context = self.versions.context(owner, event_id, subject=subject, scope=dict(scope or {}, channel=channel),
                                            scenario=scenario, focus_keys=focus_keys, exclude_keys=exclude_keys, context_id=context_id)
            # The memory this question must weigh, what it must ask and the offer to draft a question (M1). What
            # silence settled is stored with this context first, so this turn does not ask it again.
            context.update(language=language, settled=recorded.get("defaulted", []))
            self.store.put("contexts", owner, context)
            shown = self.present_pack(owner, kind="narrow", scenario=scenario, pack_id=context_id, snapshot=context["snapshot"],
                                      at=context["snapshot"]["taken_at"], subject=subject, scope=dict(scope or {}, channel=channel),
                                      live=True)
            current = self.versions.current(owner)
            # An undo is done here, in the one call the user's words arrive in (round 4, 2.1): the version goes back
            # and the records the undone turns made go with it, without asking and without a draft for the agent.
            undo = self._undo(owner, text, current, language, ctx_id=context_id)
            if undo:
                current = self.versions.current(owner)  # the version the undo wrote: the answer and the pages read it
            plan = current["plan"] or {}
            at = context["snapshot"]["taken_at"]
            known = consider.sources(self.store, owner, plan or None, at=at, language=language)
            asked = consider.asks(self.store, owner, plan or None, scenario=scenario, scope=scope, at=at, language=language, known=known)
            considered = consider.build(self.store, owner, pack=shown["pack"], plan=plan or None, scenario=scenario, at=at, language=language)
            context.update(pack=shown["pack"], asked=[dict(id=question["id"], scope=question.get("scope", {})) for question in asked],
                           options_pending=bool(recorded.get("crowding")))
            self.store.put("contexts", owner, context)
            anchors = self._hard_nodes(owner, plan, subject, scope, at, language=language)
            write = self.write_ref(owner, op)
            pending = memory.pending_entries(self.store, owner)
            possible = self.ledger.possible_contradictions(owner, context["bundle"]["items"])
            response = dict(now=self._clock(owner, at, language), request_id=receipt.get("request_id", request_id),
                            receipt=receipt, ctx_id=context_id, base_version=current["number"],
                            snapshot=context["snapshot"],
                            write=write, notices=self.open_notices(owner),
                            pack=dict(markdown=shown["markdown"], meta=shown["meta"], tokens=shown["tokens"]),
                            recorded=recorded, possible_contradictions=possible,
                            current_summary=dict(trip=self.current_trip(owner), title=plan.get("title"), days=[day["date"] for day in plan.get("days", [])],
                                                 needs_replan=current.get("needs_replan", False) or recorded["needs_replan"],
                                                 warnings=current.get("warnings", [])),
                            hard_nodes=anchors,
                            inbox=[dict(job_id=message["job_id"], state=message["state"]) for message in self.store.all("inbox", owner)
                                   if message["state"] in ("generated", "available") and message["base_version"] == current["number"]])
            # Round 4, 2.2: what a recording step used to fetch with state(view="ledger") and state(view="graph") comes
            # with the turn that records the message: the entries and decisions in force, the declared links and what
            # needs review. Lean shapes: the agent reads it to link what it records, not to render a page.
            ledger_now, links = self.ledger.current(owner, at=at), self.ledger.graph(owner)
            response["records"] = dict(
                items=[{key: item[key] for key in ("id", "kind", "key", "text", "strength", "status") if key in item}
                       for item in ledger_now["items"]],
                decisions=[{key: decision[key] for key in ("id", "question", "choice", "must", "depends_on", "locked_paths", "status")
                            if key in decision} for decision in ledger_now["decisions"]],
                links=links["text"], review=links["review"])
            if pending:
                response["memory"] = dict(pending=pending, ask=MEMORY_ASK[language])
                asked.append(dict(id="memory", say=response["memory"]["ask"], default=packs.WORDS[language]["memory_default"],
                                  record=dict(carry=[dict(id=entry["id"], status="confirmed or not_used") for entry in pending])))
            told = packs.point_facts(self.store, owner, plan or None, points, anchors, at, language,
                                     person=packs.asker(plan, speaker, subject), words=text) if points is not None and plan else None
            first = self._quick_first(text, scenario, response["now"], anchors, plan or None, told, language=language)
            # A road question with no draft to send opens with a short message instead (round 4 review): the user waits
            # for the walk, not for the lookups. "Road" is the quick scenario or the words of a latest/missed question.
            road = bool(not first and (scenario == "quick" or first_part.asks_latest(text)
                                      or first_part.MISSED.search(text or "")))
            # trip60 (the user, 2026-10-30: “每次我们在旅行部分的输出你都可以考虑两个部分 一个是及时快速的……先告诉我”): every
            # trip message opens with a short first message written before any other tool, on the road or not; the
            # service's first_part, when there is one, is material for it, not a text to send as it is (replays of
            # 2026-11-22: two of three drafts sent as they were did not answer the question).
            opens = bool(not undo and scenario != "system" and self._reply_flow() == "two")
            opening = opening_material(plan or {}, at, language) if road or opens else None
            recalled = self._recall(owner, plan or None, text, points, at, event_id, language)
            held = bool(recalled["found"] or recalled.get("catalog"))  # the repository held something (in recall or the opening)
            decision = (decide.answer(self.store, owner, event_id, receipt["received_at"], aware) if not undo
                        else dict(answered=[], opened=[]))
            decided, opened = decision["answered"], decision["opened"]
            for question in opened:  # the question that waited its turn is asked in this answer, once
                asked.append(dict(id=question["id"], say=question["say"], default=question["default"],
                                  options=question.get("options"), recommended=question.get("recommended"),
                                  then=DECIDED_LEAD[language]))
            if opening is not None and recalled["found"]:
                # what the repository holds beyond the ledger (the ledger is in consider): places, plan, bookings, checks
                shown = recall.lines(recalled, language, limit=2, kinds=("place", "plan", "booking", "web", "lookup", "said"))
                opening = opening + shown
                # what the opening lines carry is not listed twice
                recalled = dict(recalled, found=[entry for entry in recalled["found"] if entry["id"] not in {line["id"] for line in shown}])
            second_title = SECOND_TITLES[language] if first or road or opens else None
            context.update(points=packs.point_record(told),
                           frame=dict(scenario=scenario, asks_latest=first_part.asks_latest(text)),
                           request_id=request_id, promises_second=bool(first), first_text=first["text"] if first else None,
                           continues=None,
                           asked=[*(context.get("asked") or []),
                                  *[dict(id=question["id"], scope=question.get("scope", {})) for question in opened]])
            self.store.put("contexts", owner, context)
            instructions = self._next(considered, asked, recorded, first=first, second_title=second_title,
                                      latest=first_part.asks_latest(text),
                                      unknown=self._unknown_places(plan, told, language=language), language=language,
                                      scenario=scenario, options=bool(possible or recorded.get("crowding")), road=road,
                                      opens=opens)
            # Claude reads the first fields first: the immediate action and both service-written headings lead.
            if opens:
                lead = OPEN_FIRST["en" if language == "en" else "zh"]
                notes = self._first_notes(considered, asked, recorded, language=language, scenario=scenario,
                                          options=bool(possible or recorded.get("crowding")))
                lead += (" " if language == "en" else "") + notes
                if recorded.get("standing"):
                    quotes = "；".join(f"「{entry['quote']}」" for entry in recorded["standing"])
                    lead += (f" End the first message with one line: recorded as a standing rule: {quotes} (say “undo” to take it back)."
                             if language == "en" else f"第一条末尾加一行：记下了，以后照做：{quotes}（不对就回“撤回”）。")
                if language == "en":
                    lead = REPLY_IN_ENGLISH + " " + lead
                response = dict(next=lead, first_part=first, opening_material=opening, second_part_title=second_title,
                                after_first_part=AFTER_OPEN[language] + instructions, **response)
            elif first and self._reply_flow() == "two":
                first = dict(first, later=first_part.LATER[language])
                send = SEND_IN_ENGLISH if language == "en" and message_language(first["text"]) == "zh" else SEND_AND_END[language]
                response = dict(next=send, first_part=first, second_part_title=second_title,
                                after_first_part=AFTER_FIRST[language] + instructions, **response)
            else:
                response = dict(next=instructions, first_part=first, second_part_title=second_title, **response)
            if road and not opens:
                response["opening_material"] = opening
            if decided:
                response["decided"] = [{key: question.get(key) for key in ("id", "say", "options", "recommended", "default", "by")}
                                       for question in decided]
                response["next"] = whole_sentences(DECIDED_LEAD[language] + (" " if language == "en" else "") + response["next"], 1100)
            if opened:
                lead = ASK_OPEN[language].format(id=opened[0]["id"])
                response["next"] = whole_sentences(lead + (" " if language == "en" else "") + response["next"], 1100)
            if recalled["found"] or recalled.get("catalog"):
                response["recall"] = recalled
            if "after_first_part" in response:
                use = (RECALL_USE[language] if held else "") + CONFLICT_USE[language]
                response["after_first_part"] = response["after_first_part"].replace(AFTER_OPEN[language], AFTER_OPEN[language] + use, 1) \
                    if response["after_first_part"].startswith(AFTER_OPEN[language]) else response["after_first_part"] + use
            mode = consider.save_mode(self.store, owner, at=at)
            response.update(consider={key: considered[key] for key in ("entries", "more", "more_how") if key in considered},
                            ask=asked, offer=consider.offer(known["languages"], language),
                            save_mode=dict(mode=mode["mode"], by=mode["by"], how=SAVE_MODE_HOW[language]))
            review = self.ledger.needs_review(owner)
            if review:
                response["review"] = review
            waiting = self._waiting_draft(owner, event_id, language) if self.asks_first(owner) else None
            # An undo is done here, so no planning line may ask for a draft again; a road question with no first part opens
            # with a short message, and its whole flow says that too (round 4 review).
            response["flow"] = self.flow.turn(owner, clock(), scenario=scenario, language=language,
                                              second=bool(first), latest=first_part.asks_latest(text),
                                              unknown=bool(self._unknown_places(plan, told, language=language)),
                                              ids=dict(event_id=event_id, ctx_id=context_id, base_version=current["number"]),
                                              line=UNDO_LINE[language] if undo else (SHORT_ROAD[language] if road else None),
                                              full_line=FULL[language]["quick_road"] if road and not undo else None,
                                              whole=not undo)
            # The agent acts on the instruction it reads first: whom to dispatch leads it (2026-11-12 live runs: as a
            # separate field only, a short planning edit went to the requirements role 2 times in 10).
            named = response["flow"]["dispatch"]
            if named and scenario in PLANNING and opens:
                # whom to dispatch comes right after the first message, never before it (trip60)
                lead = ("Then dispatch " if language == "en" else "第一条发出后先派 ") + ("; " if language == "en" else "；").join(
                    f"{entry['role']} ({entry['why']})" if language == "en" else f"{entry['role']}（{entry['why']}）" for entry in named)
                lead += ". " if language == "en" else "。"
                response["after_first_part"] = AFTER_OPEN[language] + whole_sentences(lead + instructions, 700)
            elif named and scenario in PLANNING:  # on the road the first instruction stays the first part or the answer
                lead = ("Dispatch " if language == "en" else "先派 ") + ("; " if language == "en" else "；").join(
                    f"{entry['role']} ({entry['why']})" if language == "en" else f"{entry['role']}（{entry['why']}）" for entry in named)
                lead += ". " if language == "en" else "。"
                head = REPLY_IN_ENGLISH + " " if response["next"].startswith(REPLY_IN_ENGLISH + " ") else ""  # it opens the reply
                response["next"] = whole_sentences(head + lead + response["next"][len(head):], 600)
            if recorded.get("not_in_effect"):
                # read first: a setting the user asked for that did not take effect changes what this answer may claim
                said = "；".join(f"{entry['key']}：{entry['why']}" for entry in recorded["not_in_effect"]) if language != "en" else \
                    "; ".join(f"{entry['key']}: {entry['why']}" for entry in recorded["not_in_effect"])
                lead = (f"The setting just recorded does not take effect ({said}); see recorded.not_in_effect for how to record it again. "
                        if language == "en" else f"刚记的设置没生效（{said}），怎样重记见 recorded.not_in_effect。")
                response["next"] = whole_sentences(lead + response["next"], 900)
            if waiting:
                response["waiting_draft"] = waiting
                if not first:  # a first part keeps its own first instruction; the pointer is in waiting_draft all the same
                    response["next"] = whole_sentences(waiting["say"] + (" " if language == "en" else "") + response["next"], 600)
            if undo:
                response["undo"] = undo
                if not first:
                    said = UNDO_NEXT["en" if language == "en" else "zh"]["taken" if undo.get("taken_back") else "plain"]
                    response["next"] = whole_sentences(said.format(number=undo["restore_version"]), 700)
            if told is not None:
                response["points"] = told
            if points_note:
                response["points_note"] = points_note
        if self.committer is not None:
            response["sync"] = self.committer.status()
            self.committer.poke()
        return response

    def asks_first(self, owner: str | None = None) -> bool:
        """Whether a host agent's accept_unverified counts only after another message has arrived through turn
        (versions.commit): the cautious save mode. The user chooses it per trip or for good (a `save_mode` setting,
        consider.save_mode, 2026-11-13: two modes, the user picks); without one the default is proactive (the user,
        2026-11-14), and TRIPWRIGHT_SAVE_UNVERIFIED=ask makes it cautious for a deployment. Either way a changed must, a bought or
        booked item, a hard node and competing preferences are refused as before."""
        mode = consider.save_mode(self.store, owner, at=now()) if owner is not None else dict(mode=consider.deployment_mode())
        return mode["mode"] == "cautious"

    def _undo(self, owner: str, text: str, current: dict, language: str, *, ctx_id: str | None = None) -> dict | None:
        """A message that only takes the last change back (“撤回”, “undo”): the plan goes back to the version before the
        first version the last message made (its requirements may be a version of their own before the plan: 2026-11-14,
        ChatGPT went 1 → 3 for one message), and everything those turns recorded goes with it, in the same call. Nothing
        holds an undo: the version was saved once with the records in force then (round 4, 2.1). None when there is
        nothing to go back to."""
        versions = sorted(self.store.all("versions", owner), key=lambda version: version["number"])
        said = {event["id"]: event["received"]["text"] for event in self.versions.messages(owner)}
        target = current.get("base_version")
        event = current.get("source_event")
        words = said.get(event)
        if current.get("restored_from") is not None and words == text:
            # the same words sent again (a host retrying after a lost answer): this message's undo is already saved, and
            # the once-only cautious-mode offer is not said a second time (`replay`)
            return self._undo_said(owner, current, current["restored_from"], language, replay=True)
        for version in reversed(versions):
            if version["number"] > current.get("number", 0):
                continue
            # the same message, or the same words sent through turn again (a host retrying after a refusal)
            if event is None or not (version.get("source_event") == event or (words and said.get(version.get("source_event")) == words)):
                break
            target = version.get("base_version")
        if not UNDO_WORDS.match(text or "") or not isinstance(target, int) or target < 1 or current.get("number", 0) < 2:
            return None
        if not any(version["number"] == target and version.get("plan") for version in self.store.all("versions", owner)):
            return None
        if ctx_id is None:  # not this message's own turn: nothing to put the restored plan on
            return None
        draft = self._propose_tool(owner, ctx_id=ctx_id, base_version=current["number"], restore_version=target)
        version = self._commit_tool(owner, draft["draft_id"], f"undo:{draft['draft_id']}")
        return self._undo_said(owner, version, target, language)

    def _undo_said(self, owner: str, version: dict, target: int, language: str, *, replay: bool = False) -> dict:
        """What turn returns for an undo: the version restored, the entries taken back and the answer as the receipt has
        it (`_saved_say`), so the agent writes the user's words from the service. `replay`: the same words again, whose
        undo is already saved; its answer is the first one without the offer that is made only once."""
        draft = self.store.get("drafts", owner, version["draft_id"]) if version.get("draft_id") else None
        tell = (self._saved_say(owner, draft, version, False, replay=replay) if draft else None) or {}
        return dict(restore_version=target, version=version["number"], done=True,
                    say=tell.get("say"), how=tell.get("how"),
                    **{key: tell[key] for key in ("taken_back", "offer") if key in tell})

    def _waiting_draft(self, owner: str, event_id: str, language: str) -> dict | None:
        """The plan draft this message answers: proposed for the message before this one, still on the current
        version, with unverified facts no message had answered until now. Only the next message gets the pointer:
        it is the one versions.unanswered takes as the answer, and a draft the user never came back to does not
        follow every later turn."""
        messages = self.versions.messages(owner)
        this = next((event for event in messages if event["id"] == event_id), None)
        before = [event for event in messages if this and event["id"] != event_id and event["received"]["text"] != this["received"]["text"]]
        if not before:
            return None
        number = self.versions.current(owner)["number"]
        said = {event["id"]: event["received"]["text"] for event in messages}
        drafts = [draft for draft in self.store.all("drafts", owner)
                  if draft["status"] == "proposed" and draft.get("ask") and draft["base_version"] == number
                  and said.get(draft["source_event"]) == before[-1]["received"]["text"]]
        if not drafts:
            return None
        draft = max(drafts, key=lambda entry: aware(entry["created_at"]))
        if self.versions.unanswered(owner, draft["ask"])[0]:
            return None
        stale = bool(self.versions.fresh(owner, draft.get("snapshot")))
        words = (WAITING_STALE if stale else WAITING)["en" if language == "en" else "zh"]
        return dict(draft_id=draft["id"], unverified=[check["id"] for check in draft["ask"]], stale=stale,
                    say=words.format(draft=draft["id"], n=len(draft["ask"])))

    def _recall(self, owner: str, plan: dict | None, text: str, points, at: str, event_id: str, language: str) -> dict:
        """What the repository already holds for this message (recall.search over its text and the points' words),
        without what this message itself recorded; compact entries for the turn's `recall`."""
        words = " ".join([text or ""] + [str(point.get("said") or "") + " " + str(point.get("place") or "")
                                         for point in points or [] if isinstance(point, dict)])
        zone = (plan or {}).get("timezone") or (self.repository.timezone() if self.repository is not None else None)
        found = recall.search(self.store, owner, plan, words, at=aware(at), zone=zone, language=language, limit=RECALL_LIMIT,
                              exclude_events={event_id}, kind_cap=recall.KIND_CAP, repository=self.repository, pool=True)
        # the catalog (catalog.py): a row for every entry the repository holds, so the host can pick by meaning what
        # the lexical top few missed; computed from the same entries, without this message's own records
        listed = catalog.build(found["pool"]["entries"], at=aware(at), zone=zone, language=language, scores=found["pool"]["scores"])
        return dict(found=[dict({key: entry[key] for key in ("found", "id", "source", "path", "line", "commit", "snippet",
                                                              "snippet_line", "snippet_exact", "cited_as") if key in entry},
                                text=recall.short(entry["text"], None, RECALL_TEXT),
                                **({} if entry["in_force"] else dict(in_force=False)))
                           for entry in found["found"]],
                    **(dict(catalog=listed) if listed["lines"] or listed["omitted"] else {}))

    def _quick_first(self, text: str, scenario: str, clock: dict, anchors: list[dict], plan: dict | None,
                     told: dict | None, *, language: str | None = None) -> dict | None:
        """A quick first part only when stored data answers at least one named point, or, when the host could use none
        of its points, when the service itself reads an intent of the trip in the message."""
        if scenario != "quick" or told is None or not plan:
            return None

        def useful(point):
            facts = point["facts"]
            if point["kind"] == "go":
                return any(facts.get(key) for key in ("frame", "train", "way", "nodes"))
            if point["kind"] == "eat":
                return bool(facts.get("stored"))
            if point["kind"] == "do":
                return bool(facts.get("places"))
            if point["kind"] == "ticket":
                return bool(facts.get("tickets"))
            return False

        # The host's points come first. When it marked every point `other` (it named no place, so no stored place can
        # stand in for one it named, which A12 and S4h guard against), an intent the service reads in the words
        # themselves backs it up. One intent so far: a latest-time question gets the plan's hard nodes (H-02: no long
        # silence; 2026-11-12, GPT marked “最最迟的时间……机场能不能进去” all `other` and the first reply came 8 to 27 s
        # later). The host's `at` is a mark too: such a draft says where the plan has the user. Another intent goes
        # here, measured the same way first.
        unread = bool(told["points"]) and all(point["kind"] == "other" for point in told["points"])
        missed = None
        if not any(useful(point) for point in told["points"]):
            # the second intent: a missed connection, answered only with what the plan stores for that leg
            missed = first_part.MISSED.search(text) and packs.missed_leg(plan, clock["date"], clock["time"], told.get("person"),
                                                                         language or self.language)
            if not missed and not (unread and first_part.LATEST.search(text)):
                return None
            # drafted from the words, so where the plan has the user, said as assumed; the host's `at` only when the
            # plan has no place now
            told = dict(told, at=told.get("planned") or told["at"])
        first = (first_part.missed_draft(missed, language or self.language) if missed
                 else first_part.draft(text, clock, anchors, plan, language or self.language, told))
        kept = []
        for line in first["lines"]:
            if line.startswith(("没核：", "Not checked:")):
                continue
            promised = "第二段" in line or "part 2" in line.lower()
            stored = any(mark in line for mark in ("存档", "查过", "现在关着", "archived", "checked at", "closed now"))
            if promised and not stored:
                continue
            kept.append(line)
        if not kept:
            return None
        language = message_language(text)
        lines = [FIRST_TITLES[language], *kept[:7]]
        return dict(first, text="\n".join(lines), lines=lines)

    def _refused_record(self, record, reason: str, event_id: str, *, language: str) -> dict:
        """recorded.not_recorded for a turn.record left out as a whole (S5), with where the corrected entries go: the same
        request_id now holds this receipt, so they go into this question's propose (or the next turn's record)."""
        count = sum(len(value) for value in record.values() if isinstance(value, list)) if isinstance(record, dict) else 0
        en = language == "en"
        return dict(part="record", records=count, reason=reason,
                    fix=(f"Nothing of record was kept; the message was received. Send the corrected entries in this question's propose "
                         f"(source.event \"{event_id}\"), not again with this request_id." if en else
                         f"record 一条没记，原话已收。改好后放进这一问的 propose（source.event 写 \"{event_id}\"），不要用同一个 request_id 重发 turn。"))

    def _hard_nodes(self, owner: str, plan: dict, subject: list[str] | None, scope: dict | None, at: str, *,
                    language: str) -> list[dict]:
        """turn's hard nodes: the plan's anchors for the subject and scope, each with its latest departure clock, where
        its time comes from, its archived claims and whether it has passed (passed ones last)."""
        anchors = [dict(date=day["date"], **anchor) for day in plan.get("days", []) for anchor in day.get("anchors", [])
                   if (not subject or "*" in subject or set(subject) & set(anchor["subject"]))
                   and (not scope or (not scope.get("start") or day["date"] >= scope["start"]) and (not scope.get("end") or day["date"] <= scope["end"]))]
        versions = packs.committed(self.store.all("versions", owner))
        for anchor in anchors:
            anchor["latest_departure"] = departure_clock(anchor["time"], anchor["buffer_minutes"], anchor["travel_minutes"], day=anchor["date"], zone=plan["timezone"])
            # Where the time comes from and whether it is already behind us (A2): a passed node's consequence
            # ("only the 23:53 is left") is history, not tonight's options.
            anchor.update(packs.node_timing(self.store, owner, versions, anchor["date"], anchor, at))
            # A9 (D-64): a stored "末班 / 只剩 / 最晚" sentence is marked as archive (when written, source), never a
            # current fact; a later departure found this session voids it.
            anchor.update(packs.archived_claims(self.store, owner, plan or None, anchor["date"], anchor, anchor, at,
                                                language=language))
            instant = local_instant(anchor["date"], anchor["time"], plan["timezone"])
            anchor["passed"] = instant is not None and instant <= aware(at)
        anchors.sort(key=lambda anchor: anchor["passed"])
        return anchors

    def draft_first_part(self, owner: str, text: str, *, points: list | None = None, speaker: str | None = None,
                         subject: list[str] | None = None) -> dict:
        """turn's first part without turn, for a host whose TripWright tools are down (A16, quality judge 8, off_r1: the
        host wrote its own from the plan's files, with the stored “最晚 21:50 出门” and “23:53 末班” on its first line). It
        reads the trip and records nothing: the message is not received, no context is kept. Returns the clock, the
        draft (`first_part`, as turn's) and what to do with it (`next`)."""
        self.bound(owner)
        language = message_language(text)
        points = self._checked_places(owner, first_part.checked_points(points))
        plan = self.versions.current(owner)["plan"] or {}
        at = now()
        anchors = self._hard_nodes(owner, plan, subject, None, at, language=language) if plan else []
        told = packs.point_facts(self.store, owner, plan, points, anchors, at, language,
                                 person=packs.asker(plan, speaker, subject), words=text) if points is not None and plan else None
        clock = self._clock(owner, at, language)
        first = self._quick_first(text, "quick", clock, anchors, plan or None, told, language=language)
        en = language == "en"
        return dict(now=clock, first_part=first, second_part_title=SECOND_TITLES["en" if en else "zh"] if first else None,
                    **(dict(points=told) if told is not None else {}),
                    next=("Write first_part.text before any other tool. After checking, use second_part_title and say differences first."
                          if en else "先把 first_part.text 写出来，再调其他工具；核完用 second_part_title，先讲和第一段的不同。"))

    def _checked_places(self, owner: str, points: list[dict] | None) -> list[dict] | None:
        """Points whose place and words name two different stored places are refused before anything is recorded
        (S4h-a, ns_on_r1 question 2), with what to send instead; the same request_id may be sent again."""
        conflicts = packs.place_conflicts(self.versions.current(owner)["plan"], points) if points else []
        if conflicts:
            raise ValueError("Invalid points: " + "; ".join(conflicts))
        return points

    def _unknown_places(self, plan: dict | None, told: dict | None, *, language: str | None = None) -> list[str]:
        """What `next` says of the places in points that are no stored place and no coordinates (S4h-a, mcponly_r1
        question 1: “airport” was left unread and the first part said only that the time needs checking): which, that
        the first part names none of them and never the plan's place in their stead, and the stored IDs to send."""
        unknown = list(dict.fromkeys([point["unknown"] for point in (told or {}).get("points", []) if point.get("unknown")]
                                     + ([told["at"]["unknown"]] if (told or {}).get("at") and told["at"].get("unknown") else [])))
        if not unknown:
            return []
        en = (language or self.language) == "en"
        said = (", " if en else "、").join(f"“{value}”" for value in unknown)
        return [f"These places in points were not recognized: {said}. Continue without another turn; query by coordinates "
                f"or stop name." if en else
                f"points 里这些地方没认出：{said}。不用再调 turn，按坐标或站名查。"]

    def _asked(self, owner: str, event_id: str) -> list[dict]:
        """What the previous turn asked (the latest context of another message): must-go places, real-time data."""
        earlier = [context for context in self.store.all("contexts", owner) if context.get("source_event") != event_id]
        latest = max(earlier, key=lambda context: aware(context["created_at"]), default=None)
        return (latest or {}).get("asked") or []

    def _reply_flow(self) -> str:
        return "one" if os.environ.get("TRIPWRIGHT_REPLY_FLOW", "two").lower() == "one" else "two"

    def _first_notes(self, considered: dict, asked: list[dict], recorded: dict, *, language: str | None = None,
                     scenario: str | None = None, options: bool = False) -> str:
        """What shapes the first message itself, after OPEN_FIRST in `next` (trip60): competing preferences become
        options, the questions in `ask` are asked, and a replaced entry is said. No lookup and no state read here; the
        entry-by-entry weighing of consider (which in the replays sent hosts reading the ledger first) and every lookup
        follow the first message in `after_first_part`."""
        en = (language or self.language) == "en"
        notes = []
        if options and scenario in ("plan", "compare"):
            notes.append("These preferences compete: give two or three options with the recommended one marked, record the "
                         "user's words, but do not change the plan until the user chooses an option." if en else
                         "这些偏好互相挤：第一条给两三套方案、标出推荐的；这一条只记用户说的偏好，不改计划；等用户选了一套再改。")
        if recorded.get("replaced"):
            w = consider.words(language or self.language)
            notes.append(("Say what changed: " if en else "回答里说一句这次改了什么：")
                         + w["join"].join(f"{line['old']} → {line['new']}" for line in recorded["replaced"]) + ("." if en else "。"))
        if asked:
            notes.append(consider.words(language or self.language)["ask_next"])
        notes.extend(considered.get("hints", []))  # whose line is whose, and the user's line wins: the first message too
        separator = " " if en else ""
        kept = []
        for note in notes:
            if len(separator.join([*kept, note])) <= 420:
                kept.append(note)
        return separator.join(kept)

    def _next(self, considered: dict, asked: list[dict], recorded: dict, *, first: dict | None,
              second_title: str | None, latest: bool = False, unknown: list[str] = (), language: str | None = None,
              scenario: str | None = None, options: bool = False, road: bool = False, opens: bool = False) -> str:
        """Only this message's next actions, bounded for a chat tool result. With `opens` the first message's
        instruction is turn's `next` (OPEN_FIRST), and this is what follows it."""
        en = (language or self.language) == "en"
        parts = []
        if first and self._reply_flow() == "one":
            parts.append("Send first_part.text to the user now (the user is waiting); call query only after sending it." if en else
                         "现在就把 first_part.text 发给用户（用户在等这第一段）；发出去之后再调 query。")
        if road and not opens:
            # round 4 review: no first part on the road is no reason to go silent through the lookups (baseline
            # 2026-11-19: the first text came 4 to 19 minutes after the question in 13 of 15 runs). Review 4: the one
            # sentence comes first, and its material is stored data, not a lookup.
            parts.append(ROAD_OPENING["en" if en else "zh"])
        if en and not opens:
            parts.append(REPLY_IN_ENGLISH)
        # Round 4 review, the user's order (2026-10-29): the answer first, then the save; the page follows the answer.
        parts.append(SAY_BEFORE_SAVE["en" if en else "zh"])
        if opens:
            parts.append(SEPARATE_PARTS["en" if en else "zh"] + FIRST_ARCHIVED["en" if en else "zh"])
            parts.append(WORK_NOTES["en" if en else "zh"])
        elif first:
            parts.append(f"After checking, write {second_title!r} as the second heading; first say what differs "
                         "from first_part, or 'Same as above, checked.'" if en else
                         f"核完后写第二段标题“{second_title}”；标题下先讲和第一段的不同，没有不同就写“和上面一样，已核”。")
            parts.append(SEPARATE_PARTS["en" if en else "zh"] + FIRST_ARCHIVED["en" if en else "zh"])
            parts.append(WORK_NOTES["en" if en else "zh"])
        elif road:
            parts.append(SEPARATE_PARTS["en" if en else "zh"])
            parts.append(WORK_NOTES["en" if en else "zh"])
        else:
            parts.append("Answer this message directly after its necessary lookups; do not invent a first part."
                         if en else "完成必要的查询后直接答这一问，不造第一段。")
            parts.append(WORK_NOTES["en" if en else "zh"])
        if options and scenario in ("plan", "compare") and not opens:
            parts.append("These preferences compete: record the user's words, but do not change the plan until the user chooses "
                         "an option." if en else "这些偏好互相挤：这一条只记用户说的偏好，不改计划；等用户选了一套再改。")
        elif options and scenario in ("plan", "compare"):
            parts.append("Do not change the plan until the user chooses an option." if en else "等用户选了一套再改计划。")
        elif scenario in ("plan", "replan"):
            parts.append("For an actual plan edit: state(view='day',date=...), edit its json, propose(days=[json]), commit; "
                         "do not resend the whole trip." if en else
                         "真要改计划：state(view='day',date=...)，改返回的 json，propose(days=[json])，再 commit；不重发整份行程。")
        refused = [line["fix"] for line in recorded.get("not_recorded", []) if line.get("part") == "record"]
        if refused:
            parts.append(("record: " if en else "record 没记：") + refused[0])
        if recorded.get("replaced") and not opens:
            w = consider.words(language or self.language)
            parts.append(("Say what changed: " if en else "回答里说一句这次改了什么：")
                         + w["join"].join(f"{line['old']} → {line['new']}" for line in recorded["replaced"]) + ("." if en else "。"))
        # What fills the rest of the bound, most useful first. The blind comparison of 2026-11-11 put the web line here:
        # without it hosts stated web findings they had not fetched through the service.
        optional = []
        if latest:
            optional.append("For tonight's latest, call query(kind='latest_departure', arguments={origin,destination}) and use "
                            "its first_line and window." if en else
                            "问今晚最晚：query(kind='latest_departure', arguments={origin,destination})，照它的 first_line、sentences 和 window 说。")
        if first or road or opens:
            optional.append("Something found online: fetch its sentence with query(kind='web_evidence') first, or label it "
                            "unchecked; no process remarks." if en else
                            "网上查到的说法先用 query(kind='web_evidence') 取到原句再写，取不到就标没核；不写过程话。")
        optional.extend([*unknown, considered["next"]])
        if asked and not opens:
            optional.append(consider.words(language or self.language)["ask_next"])
        if considered.get("more_how"):
            optional.append(considered["more_how"])
        separator = " " if en else ""
        for part in optional:
            if len(separator.join([*parts, part])) <= 600:
                parts.append(part)
        # The first/title/refusal instructions are mandatory; a pathological server-side refusal is still bounded.
        return separator.join(parts)[:600]

    def _record(self, owner: str, record: dict, event_id: str | None = None, *, language: str) -> tuple[dict, dict]:
        """Apply what a message states in the same transaction as its receipt, so this turn's package already has it.
        Facts never wait for the rest of the message (B3c): an invalid other part leaves them recorded alone. An item
        that would replace a stable entry without the user's own words in this message is left out and listed in
        not_recorded with what to ask (M1); `replaced` lists the entries the others replaced."""
        extra = [part for part, value in record.items() if value and part not in TURN_RECORDS]
        if extra:
            raise ValueError(f"turn records {', '.join(TURN_RECORDS)}; send {', '.join(extra)} with propose and commit")
        try:
            changes = Changes.model_validate(record)
        except ValidationError as error:
            # Each problem with its path in the record, what it needs and what was sent (B3f X1a), e.g.
            # `record.facts.0.true_from: must be an ISO time with a timezone offset (…), got 'this'`.
            raise ValueError("Invalid record: " + "; ".join(input_problems(error, "record"))) from None
        plan = self.versions.current(owner)["plan"]
        if plan is not None:
            people = {person["id"] for person in plan["people"]} | {"*"}
            for entry in [*changes.items, *changes.facts, *changes.pending_checks]:
                if not set(entry.subject) <= people:
                    raise ValueError("Ledger subject is outside trip people")
        not_recorded = []
        refused = [(item, self.ledger.stable_refusal(owner, item, Proposal(answered=event_id))) for item in changes.items]
        refused = [(item, reason) for item, reason in refused if reason]
        if refused:
            changes = changes.model_copy(update=dict(items=[item for item in changes.items if item not in [entry for entry, _ in refused]]))
            not_recorded.append(dict(part="items", records=len(refused), ids=[item.id for item, _ in refused],
                                     reason="; ".join(reason for _, reason in refused),
                                     ask="这条定了基本不变，用户本轮没说要改：先问用户，用户说了再记。" if language != "en" else
                                         "This entry is set once and the user did not say to change it this turn: ask the user first."))
        dumped = changes.model_dump(mode="json")
        try:
            with self.store.attempt():
                applied = self.ledger.apply(owner, dumped, answered=event_id)
        except ValueError as error:
            others = [part for part in TURN_RECORDS if part != "facts" and dumped.get(part)]
            if not (changes.facts and others):
                raise
            not_recorded += [dict(part=part, records=len(dumped[part]), reason=str(error)) for part in others]
            dumped = Changes(facts=changes.facts).model_dump(mode="json")
            applied = self.ledger.apply(owner, dumped, answered=event_id)
        warnings = []
        if plan is not None and (changes.facts or changes.items):
            report = check_plan(self.store, owner, Trip.model_validate(plan))
            warnings = [check for check in report["checks"] if check["status"] == "conflict" and check["critical"]]
        replaced = []
        for item in changes.items:
            for old_id in self.store.get("items", owner, item.id).get("relations", {}).get("supersedes", []) if item.id in applied["changed"] else []:
                old = self.store.get("items", owner, old_id)
                kind = consider.kind_of(old)
                replaced.append(dict(id=item.id, replaces=old_id, kind=kind, stability=consider.stability(kind), old=old["text"], new=item.text))
        alongside = [dict(id=entry["id"], alongside=entry["alongside"], note=ALONGSIDE[language].format(**entry))
                     for entry in applied.get("alongside", []) if entry["id"] in applied["changed"]]
        return dumped, dict(changed=applied["changed"], not_recorded=not_recorded, warnings=warnings, needs_replan=bool(warnings),
                            crowding=applied["crowding"], **(dict(replaced=replaced) if replaced else {}),
                            **(dict(alongside=alongside) if alongside else {}))

    def present_pack(self, owner: str, **arguments) -> dict:
        pack = packs.build(self.store, owner, trip_folder=self.trip_folder(owner), language=self._reply_language(owner), **arguments)
        return packs.present(pack)

    def clock(self, owner: str, at: str | None = None) -> dict:
        return self._clock(owner, at, self._reply_language(owner))

    def _clock(self, owner: str, at: str | None, language: str) -> dict:
        """What "now" is for an answer (A2): the moment of the read in the trip's time zone (the plan's, else the
        repository's default, else UTC), with the sentence the pack's first line uses."""
        from tripwright.render.pack import now_line

        plan = self.versions.current(owner)["plan"]
        zone = plan["timezone"] if plan else (self.repository.timezone() if self.repository is not None else None) or "UTC"
        found = packs.clock(at or now(), zone)
        return dict(found, text=now_line(found, language))

    def _flowed(self, owner: str, tool: str, response, extra: list[dict] | None = None):
        """Every tool result but turn's carries one short reminder of the flow, and whom to dispatch when a step names
        someone (2026-11-12; flow.py)."""
        if not isinstance(response, dict):
            return response
        return dict(response, flow=self.flow.tool(owner, clock(), tool, language=self._reply_language(owner), extra=extra))

    def _reply_language(self, owner: str) -> str:
        return (self.store.latest("contexts", owner) or {}).get("language", self.language)

    def _answer_instruction(self, owner: str) -> dict | None:
        """The first text itself for one-reply hosts, never a second-message flow's state."""
        if self._reply_flow() == "two":
            return None
        latest = self.store.latest("contexts", owner, field="first_text")
        if latest is None:
            return None
        text = self.store.get("events", owner, latest["source_event"])["received"]["text"]
        instruction = ("还没把第一段发给用户的话，最终回答以这段开头，再写 second_part_title 和第二段。"
                       if message_language(text) == "zh" else
                       "If you have not sent the first part, start the final response with this text, then write "
                       "second_part_title and the second part.")
        return dict(text=latest["first_text"], instruction=instruction)

    async def query(self, owner: str, kind: str, arguments: dict, *, from_host: bool = False) -> dict:
        """`from_host`: the call came through the MCP tools, so a lookup no recent turn led to opens with `next`
        (flow.unturned); the command line leaves it False."""
        unturned = self.flow.unturned(owner, clock(), self._reply_language(owner)) if from_host else None
        response = self._flowed(owner, "query", await self._query_tool(owner, kind, arguments))
        return dict(next=unturned, **response) if unturned and isinstance(response, dict) and "next" not in response else response

    async def _query_tool(self, owner: str, kind: str, arguments: dict) -> dict:
        self.bound(owner)
        language = self._reply_language(owner)
        plan = self.versions.current(owner)["plan"]
        if kind == "conflict":
            return self._conflict(owner, arguments, plan, language)
        arguments = checked_arguments(kind, arguments, plan)
        if kind == "transit" and arguments.get("action") == "plan_day":
            from tripwright.solver import prepare

            day = prepare(arguments["day"])
            job = self.jobs.enqueue(owner, "plan_day", dict(day=day, timeout=min(300, max(0.01, float(arguments.get("timeout", 120))))),
                                    self.versions.current(owner)["number"], arguments["idempotency_key"])
            return dict(job_id=job["id"], status=job["status"], notice="Retrieve with result; cancel with result(cancel=true). Solver results require a separate plan proposal.")
        draft_id = arguments.get("target", {}).get("draft_id")
        if draft_id:
            draft = self.store.get("drafts", owner, draft_id)
            if draft["base_version"] != self.versions.current(owner)["number"] or draft["revision"] != self.ledger.revision(owner):
                raise ValueError("Fact target draft is stale; propose again")
            plan = draft["plan"]

        # Local times in the answer are the trip's (A5): the plan's zone, else the repository's default.
        zone = (plan or {}).get("timezone") or (self.repository.timezone() if self.repository is not None else None)
        if kind == "latest_departure":
            self._keep_frame(owner, arguments, plan, zone)

        async def lookup():
            async with asyncio.timeout(self.query_timeout):
                return await self.query_router.query(owner, kind, arguments, plan, zone=zone, at=now(), language=language)

        before = {record["id"] for record in self.store.all("evidence", owner)}
        try:
            result = await asyncio.wrap_future(self.jobs.submit(lookup()))
        except TimeoutError:
            # A11: a timed-out departure lookup says nothing was checked; “retain the plan” sent a host to its stored “末班”
            return {**dict(status="unverified", reason="The data-source query exceeded its time budget.",
                           consequence="This fact could not be verified in time.", alternative="Confirm locally or retain the previous safe plan."),
                    **(lookup_failed("timeout", language) if kind in ("latest_departure", "transit") else {})}
        self.record_evidence(owner, before, key=f"query:{identifier('Q')}")
        instruction = self._answer_instruction(owner)
        return dict(reply_begins_with=instruction, **result) if instruction else result

    def _keep_frame(self, owner: str, arguments: dict, plan: dict | None, zone: str | None) -> None:
        """A quick question whose turn asked for the latest departure is framed by the service (A10, D-67): its lookups
        name no hard node (S4h-a, stage 4 exit judge, codex_r1 question 1: the host added the plan's 05:45 queue as
        anchor_id after the frame's lookup, and its second part moved the user's night at the airport to leaving at
        dawn). Within FRAME_MINUTES of that turn (a “继续” turn carries it), latest_departure with anchor_id, or with
        `by` or `board_at` at a hard node's own time, is refused with what to send. A time the user names is not a hard
        node's and passes, as do other scenarios and lookups after the half hour."""
        from tripwright.query import anchors as plan_anchors, moment

        named = [key for key in ("anchor_id", "by", "board_at") if arguments.get(key)]
        if not named:
            return
        at = aware(now())
        written = [context for context in self.store.all("contexts", owner) if aware(context["created_at"]) <= at + timedelta(minutes=1)]
        latest = max(reversed(written), key=lambda context: aware(context["created_at"]), default=None)  # the later written of a tie
        frame = (latest or {}).get("frame") or {}
        if not frame.get("asks_latest") or SKELETON.get(frame.get("scenario")) != "quick" or \
                aware(latest["created_at"]) < at - timedelta(minutes=FRAME_MINUTES):
            return
        if "anchor_id" in named:
            key, node = "anchor_id", arguments["anchor_id"]
        else:
            key = named[0]
            try:
                instant = moment(arguments[key], arguments.get("date"), ZoneInfo(arguments.get("timezone") or zone or "UTC"), key)
            except ValueError:
                return  # its own check says what is wrong with it
            node = next((anchor["id"] for _, anchor, found in plan_anchors(plan) if found == instant), None)
            if node is None:
                return
        request = latest.get("continues") or latest.get("request_id")
        raise ValueError(f"Invalid query arguments: arguments.{key}: this question (turn request_id {request}) asks for the latest "
                         f"departure, and the service frames it: send origin and destination only (no anchor_id, by or board_at"
                         f"{f'; {arguments[key]!r} is hard node {node!r}' if key != 'anchor_id' else ''}). The result gives "
                         f"tonight's last connection and the next time to keep after it with when to leave for it (hard_node); "
                         f"the second part opens with its first_line.")

    def _conflict(self, owner: str, arguments, plan: dict | None, language: str) -> dict:
        """query(kind="conflict") (decide.py): one question for a conflict found after the plan was decided, stored in
        the repository like evidence (an outbox operation carries it), with `say` ready to send."""
        if not isinstance(arguments, dict):
            raise ValueError(f"arguments must be an object, got {json_type(arguments)}")
        evidence = None
        if arguments.get("evidence_id"):
            evidence = self.store.get("evidence", owner, arguments["evidence_id"])
        written = [context for context in self.store.all("contexts", owner)]
        latest = max(written, key=lambda context: aware(context["created_at"]), default=None)
        zone = (plan or {}).get("timezone") or (self.repository.timezone() if self.repository is not None else None)

        def local(value):
            if not value:
                return None
            moment = aware(value)
            return (moment.astimezone(ZoneInfo(zone)) if zone else moment).strftime("%m-%d %H:%M")

        question = decide.record(self.store, owner, plan, arguments, evidence=evidence,
                                 source_event=(latest or {}).get("source_event"), language=language, local=local)
        if self.committer is not None and not question.get("reused"):
            self.take_in()
            with self.store.transaction():
                self.outbox.add(owner, kind="job", tool="job", key=f"conflict:{question['id']}", records=[question["id"]],
                                replay=dict(undecided=[question]))
            self.committer.poke()
        how = DECIDE_HOW["en" if language == "en" else "zh"]
        queued = question.get("status") == "pending"
        if queued:
            how = QUESTION_WAITS["en" if language == "en" else "zh"].format(id=question["queued_after"])
        return dict(status="recorded", kind="conflict", id=question["id"], say=question["say"], options=question["options"],
                    recommended=question["recommended"], default=question["default"], by=question.get("by"),
                    reused=bool(question.get("reused")), queued=queued, ask_now=not queued,
                    **({"queued_after": question["queued_after"]} if queued else {}), next=how)

    def record_evidence(self, owner: str, before: set[str], *, key: str):
        """Evidence written by a lookup or a deep job is canonical (evidence/); an outbox operation carries it so a
        rejected push can replay it."""
        if self.committer is None:
            return
        self.take_in()
        with self.store.transaction():
            added = [record for record in self.store.all("evidence", owner) if record["id"] not in before]
            if added:
                self.outbox.add(owner, kind="job", tool="job", key=key, records=[record["id"] for record in added],
                                replay=dict(evidence=added))

    async def _requery(self, owner: str, plan: dict):
        before = {record["id"] for record in self.store.all("evidence", owner)}
        try:
            async with asyncio.timeout(self.deep_timeout):
                return await self.query_router.recheck(owner, plan)
        except TimeoutError:
            raise ValueError("Deep verification exceeded its time budget; unresolved facts remain unverified") from None
        finally:
            self.record_evidence(owner, before, key=f"deep:{identifier('Q')}")

    async def _plan_day(self, owner: str, payload: dict):
        from tripwright.providers.common import request_scope
        from tripwright.solver import solve_day

        if not all(hasattr(self.query_router.transit, method) for method in ("resolve", "day")):
            return dict(status="unverified", reason="Day solving requires a configured whole-day transit provider.")
        with request_scope(client=owner):
            return await solve_day(payload["day"], provider=self.query_router.transit, timeout=payload["timeout"])

    def propose(self, owner: str, **arguments) -> dict:
        arguments = self._this_message(owner, arguments)
        # G16: a client may leave the bookkeeping out. The key is made from the whole call here, before the
        # requirements are split off, so the same call sent again finds both of its drafts.
        arguments = dict(arguments, idempotency_key=arguments.get("idempotency_key") or proposal_key(arguments))
        arguments, first = self._requirements_first(owner, arguments)
        response = self._propose_tool(owner, **arguments)
        if first:
            response = dict(response, recorded_first=first)
            if first.get("committed") and response["status"] == "rejected":
                # The requirements are already in the ledger: say so, not "nothing will be recorded", so the agent
                # neither tells the user nothing was kept nor proposes the same entries again.
                reasons = [reason.replace("commit preferences without a plan, then change the plan after the user chooses",
                                          "they are recorded; change the plan after the user chooses")
                           for reason in self.store.get("drafts", owner, response["draft_id"]).get("refused") or []]
                response["notice"] = (f"The message's requirements are recorded (version {first['version']}); only the plan change "
                                      "was refused" + (": " + "; ".join(reasons) if reasons else "") + ".")
        draft = self.store.get("drafts", owner, response["draft_id"])
        scenario = self.store.get("contexts", owner, draft["ctx_id"])["bundle"]["scenario"]
        ids = dict(draft_id=response["draft_id"], job_id=draft.get("job_id"), ctx_id=draft["ctx_id"], event_id=draft["source_event"],
                   base_version=self.versions.current(owner)["number"])
        named = self.flow.after_propose(draft, scenario, self._reply_language(owner), ids)
        return self._flowed(owner, "propose", response, named)

    def _restored(self, owner: str, number: int, arguments: dict) -> dict:
        """`propose(restore_version=N)`: kept for hosts that still send it (round 4, 2.1: `turn` now does an undo itself).
        The draft is version N's whole plan with the records the turns after it made taken back, through the same commit
        as an undo from `turn`; nothing holds it."""
        if any(arguments.get(name) is not None for name in ("plan", "days", "places", "bookings", "blocks")):
            raise ValueError("restore_version takes that version's whole plan; send no plan, days, places, bookings or blocks with it")
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError(f"restore_version is a version number, got {number!r}")
        old = next((version for version in self.store.all("versions", owner) if version["number"] == number), None)
        if old is None or not old.get("plan"):
            raise ValueError(f"No saved plan in version {number}; state(view='history') lists the versions")
        changes = dict(arguments.get("changes") or {})
        english = self._reply_language(owner) == "en"
        changes.setdefault("why", (f"restores version {number}" if english else f"恢复到第 {number} 版"))
        named = {entry.get("id") for entry in changes.get("withdraw") or [] if isinstance(entry, dict)}
        taken = [dict(id=record["id"], filled_by="service",
                      reason=(f"taken back with the undo to version {number}" if english else f"随撤回到第 {number} 版撤掉"))
                 for record in self._undone_records(owner, number) if record["id"] not in named]
        if taken:
            changes["withdraw"] = [*(changes.get("withdraw") or []), *taken]
        return dict(arguments, plan=encode_copy(old["plan"]), changes=changes)

    def _undone_events(self, owner: str, number: int, *, besides: str | None = None) -> set:
        """The messages whose versions came after version `number` (the turns an undo to it takes back)."""
        return {version.get("source_event") for version in self.store.all("versions", owner)
                if version["number"] > number and version["id"] != besides} - {None}

    def _undone_records(self, owner: str, number: int) -> list[dict]:
        """The active records the versions after `number` recorded: requirements, decisions and facts. An undo takes all
        of them back, not only the ones the service can tell for certain broke something (round 4, 2.1: a judgement
        made on a wrong plan must not stand in the way). A record from before `number` is never taken."""
        events = self._undone_events(owner, number)
        return [record for table in ("items", "decisions", "facts") for record in self.store.all(table, owner)
                if record.get("status") == "active" and record["source"].get("event") in events]

    def _settings_not_in_effect(self, owner: str, items: list[dict], language: str) -> list[dict]:
        """The settings among `items` that do not take effect, each with why and how to record it again
        (consider.setting_not_in_effect)."""
        at = now()
        return [found for item in items if item.get("kind") == consider.SETTING
                for found in [consider.setting_not_in_effect(self.store, owner, item, at=at, language=language)] if found]

    def _this_message(self, owner: str, arguments: dict) -> dict:
        """`source.event: "this"` in propose's changes is the message of its `ctx_id`, as it is in turn's record
        (2026-11-12 runs: 7 of 12 first proposals sent "this" and were refused). Any other value is left to the checks."""
        changes, ctx_id = arguments.get("changes"), arguments.get("ctx_id")
        if not isinstance(changes, dict) or not ctx_id:
            return arguments
        named = [entry for part in changes.values() if isinstance(part, list) for entry in part
                 if isinstance(entry, dict) and isinstance(entry.get("source"), dict) and entry["source"].get("event") == "this"]
        if not named:
            return arguments
        try:
            event_id = self.store.get("contexts", owner, ctx_id)["source_event"]
        except ValueError:
            return arguments  # an unknown ctx_id is refused by propose itself
        changes = encode_copy(changes)
        for part in changes.values():
            for entry in part if isinstance(part, list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("source"), dict) and entry["source"].get("event") == "this":
                    entry["source"]["event"] = event_id
        return dict(arguments, changes=changes)

    def _message_recorded(self, owner: str, event_id: str) -> bool:
        return any((record.get("source") or {}).get("event") == event_id
                   for table in ("items", "facts", "decisions") for record in self.store.all(table, owner))

    def _requirements_first(self, owner: str, arguments: dict) -> tuple[dict, dict | None]:
        """A draft that both records what the message states and changes the plan: the records are committed first,
        as their own ledger-only version, and the plan is proposed on top of it (2026-11-12, the user: "these
        requirements must never be lost"). Before, the verifier finding the plan incomplete left the whole draft
        uncommitted, and the requirements with it (6 of 10 live runs). The records are what the user said; they need
        no plan check, as a turn's record needs none. When they cannot be committed alone, they stay in the draft."""
        changes = arguments.get("changes") or {}
        records = {part: changes[part] for part in TURN_RECORDS if changes.get(part)}
        if not records or all(arguments.get(part) is None for part in ("plan", "days", "places", "bookings", "blocks", "restore_version")):
            return arguments, None
        key = f"{arguments.get('idempotency_key')}:requirements"
        done = next((draft for draft in self.store.all("drafts", owner) if draft.get("idempotency_key") == key), None)
        rest = {part: value for part, value in changes.items() if part not in records}
        if done is not None and done.get("status") == "committed":
            # the same call sent again (a client retrying): the records went first then; send the rest as then
            version = next((v["number"] for v in self.store.all("versions", owner) if v.get("draft_id") == done["id"]), None)
            return dict(arguments, changes=rest or None), dict(committed=True, draft_id=done["id"], version=version,
                                                                parts=sorted(records))
        context = self.store.get("contexts", owner, arguments.get("ctx_id")) if arguments.get("ctx_id") else None
        if context is None or self._message_recorded(owner, context["source_event"]):
            return arguments, None  # already in the ledger (the turn recorded it): nothing to put first
        try:
            ledger = self._propose_tool(owner, ctx_id=arguments.get("ctx_id"), base_version=arguments.get("base_version"),
                                        changes=records, idempotency_key=key)
            if ledger["status"] != "proposed" or ledger.get("not_recorded"):
                return arguments, dict(committed=False, draft_id=ledger["draft_id"], reason=ledger.get("notice") or ledger["status"])
            version = self._commit_tool(owner, ledger["draft_id"], key)
        except ValueError as error:
            return arguments, dict(committed=False, reason=str(error))
        return dict(arguments, changes=rest or None), dict(committed=True, draft_id=ledger["draft_id"], version=version["number"],
                                                            parts=sorted(records),
                                                            **({k: version[k] for k in ("settings_not_in_effect",) if k in version}))

    def _propose_tool(self, owner: str, **arguments) -> dict:
        self.bound(owner)
        changes = arguments.get("changes")
        if isinstance(changes, dict) and isinstance(changes.get("withdraw"), list):
            # only the service marks what it takes back with an undo (filled_by=service); a host's mark is dropped
            arguments["changes"] = dict(changes, withdraw=[{key: value for key, value in entry.items() if key != "filled_by"}
                                                           if isinstance(entry, dict) else entry for entry in changes["withdraw"]])
        restored = arguments.pop("restore_version", None)
        if restored is not None:
            arguments = self._restored(owner, restored, arguments)
        partial = [name for name in ("days", "places", "bookings", "blocks") if arguments.get(name) is not None]
        for name in ("days", "places", "bookings", "blocks"):
            if name not in partial:
                arguments.pop(name, None)
        if partial:
            if arguments.get("plan") is not None:
                raise ValueError("Send either the complete plan or days/places/bookings/blocks patches, not both")
            patch = {name: arguments.pop(name) for name in partial}
            current = self.versions.current(owner)["plan"]
            arguments["plan"] = merged_plan(current, days=patch.get("days") or [], places=patch.get("places") or [],
                                            bookings=patch.get("bookings") or [])
            if "blocks" in patch:  # after the whole days, so a block of a day sent whole can still be changed
                arguments["plan"] = merged_blocks(arguments["plan"], patch["blocks"])
        problems = []
        if arguments.get("plan") is not None:
            try:
                checked_plan(arguments["plan"])
            except ValueError as error:
                problems.append(str(error))
            for index, place in enumerate(arguments["plan"].get("places") or []):
                # a wrong shape (2026-11-12: hours_source sent as a sentence) is already in problems from checked_plan;
                # reading it as a mapping here raised AttributeError, and the host got only “unexpected server error”
                source = place.get("hours_source") if isinstance(place, dict) else None
                cited = source.get("evidence_id") if isinstance(source, dict) else None
                if cited and not self._has("evidence", owner, cited):
                    problems.append(f"Invalid plan: plan.places.{index}.hours_source.evidence_id: no evidence {cited!r} in this trip; "
                                    "record the page with query(kind=\"web_evidence\") first")
        arguments["changes"] = normalize_record_people(arguments.get("changes") or {}, arguments.get("plan") or
                                                         self.versions.current(owner)["plan"], "changes")
        try:
            Changes.model_validate(arguments["changes"])
        except ValidationError as error:
            problems.append("Invalid changes: " + "; ".join(input_problems(error, "changes")))
        if arguments.get("usages") is not None:
            try:
                arguments["usages"] = checked_usages(arguments["usages"])
            except ValueError as error:
                problems.append(str(error))
        if problems:
            raise ValueError("Invalid proposal: " + " | ".join(problems))
        trip = self.current_trip(owner)
        if self.repository is not None and arguments.get("plan") is not None and trip and arguments["plan"].get("id") != trip:
            raise ValueError(f"plan.id must be the current trip {trip!r}; open another trip with turn(new_trip=...)")
        self.take_in()
        draft = self.versions.propose(owner, restore_from=restored, **arguments)
        context = self.store.get("contexts", owner, draft["ctx_id"])
        scenario = context["bundle"]["scenario"]
        if not ledger_only(draft) and scenario in ("plan", "compare") and (context.get("options_pending") or draft["checks"].get("crowding")):
            # Keep both preferences on record only through a separate ledger draft; never choose a plan for the user.
            draft.update(options_pending=True, status="rejected",
                         refused=[*draft.get("refused", []),
                                  "Competing preferences need the user's option; commit preferences without a plan, then change the plan after the user chooses"])
            self.store.put("drafts", owner, draft)
        if "verify" not in draft:
            with self.store.transaction():
                draft = self.store.get("drafts", owner, draft["id"])
                pack_id = identifier("CTX")
                shown = self.present_pack(owner, kind="wide", scenario=self.store.get("contexts", owner, draft["ctx_id"])["bundle"]["scenario"],
                                          pack_id=pack_id, snapshot=draft["snapshot"], at=draft["snapshot"]["taken_at"], candidate=draft["id"])
                draft["verify"] = dict(pack=shown["pack"], markdown=shown["markdown"], meta=shown["meta"], tokens=shown["tokens"])
                self.store.put("drafts", owner, draft)
        unchanged_plan = ledger_only(draft)
        notice = None
        if draft.get("refused"):
            # D-51: a user decision that only touches an old lock refuses the proposal; lock_changes says where to decide again.
            notice = "Refused, nothing will be recorded: " + "; ".join(draft["refused"]) + "."
        elif draft.get("not_recorded"):
            notice = ("Facts will be recorded; not recorded: " + "; ".join(
                f"{part['part']} ({part['records']}): {part['reason']}" for part in draft["not_recorded"])
                + ". Correct those parts and propose them again.")
        elif unchanged_plan and draft["checks"]["conflicts"]:
            notice = "The ledger change will be recorded; the unchanged plan conflicts with it and will be marked needs_replan."
        elif draft["status"] == "rejected" and draft["changes"].get("facts"):
            notice = "A rejected plan proposal records nothing, including its facts; propose the facts without a plan to record them now."
        # What the proposal does to user locks, said in the answer (D-36, D-49): made from the operations the ledger
        # admitted in the preview (ledger.lock_receipt); a correction waits for the user.
        locks = draft.get("lock_changes")
        asks = self.asks_first(owner)
        ask = self.versions.unanswered(owner, draft.get("ask") or [])[0] if asks else draft.get("ask") or []
        words = ASK_WITH_REFUSAL if draft["status"] != "proposed" else ASK_USER if asks else TELL_USER
        language = "en" if self._reply_language(owner) == "en" else "zh"
        asking = {"ask_user" if asks else "tell_user": dict(
            unverified=[{key: check[key] for key in ("id", "text", "details", "consequence", "alternative")} for check in ask],
            how=words[language].format(n=len(ask)),
            **({} if words is ASK_WITH_REFUSAL else dict(options=save_options(asks, len(ask), draft["base_version"], language))),
            **({} if asks or words is ASK_WITH_REFUSAL else dict(say=UNDO_SAY[language])))} if ask else {}
        response = dict(draft_id=draft["id"], base_version=draft["base_version"], status=draft["status"], **asking,
                    checks=draft["checks"], job_id=draft.get("job_id"), usages=draft["usages"], snapshot=draft["snapshot"],
                    **(dict(lock_changes=locks) if locks else {}), changelog=draft.get("changelog", []),
                    warnings=draft["checks"]["conflicts"] if unchanged_plan else [],
                    not_recorded=draft.get("not_recorded", []), notice=notice, crowding=draft["checks"].get("crowding", []),
                    possible_contradictions=draft["checks"].get("possible_contradictions", []),
                    verify=dict(pack_id=draft["verify"]["pack"]["pack_id"], tokens=draft["verify"]["tokens"],
                                how=f"state(view='pack', draft_id='{draft['id']}') returns the wide pack and the complete candidate for the verifier"))
        return response

    def commit(self, owner: str, draft_id: str, idempotency_key: str | None = None, *, accept_unverified: bool = False,
               ask_first: bool = False, wait: float | None = None) -> dict:
        """`ask_first`: the call comes from a host agent, so accept_unverified counts only after another message has
        arrived through turn (versions.commit). The MCP adapter sets it from asks_first(); code that seeds or tests a
        trip vouches for the user itself."""
        return self._flowed(owner, "commit", self._commit_tool(owner, draft_id, idempotency_key, ask_first=ask_first,
                                                               accept_unverified=accept_unverified, wait=wait))

    def _commit_tool(self, owner: str, draft_id: str, idempotency_key: str | None = None, *, accept_unverified: bool = False,
                     ask_first: bool = False, wait: float | None = None) -> dict:
        self.bound(owner)
        idempotency_key = idempotency_key or commit_key(draft_id, accept_unverified)
        with self.store.transaction():
            prior = self.outbox.find(owner, "commit", idempotency_key)
            version = self.versions.commit(owner, draft_id, idempotency_key, accept_unverified=accept_unverified,
                                           ask_first=ask_first)
            if prior is None:
                draft = self.store.get("drafts", owner, draft_id)
                context = self.store.get("contexts", owner, draft["ctx_id"])
                prior = self.outbox.add(owner, kind="commit", tool="commit", key=idempotency_key, draft_id=draft_id,
                                        version_id=version["id"], records=list(draft["changes"].get("items", []) and
                                                                               [item["id"] for item in draft["changes"]["items"]]),
                                        replay=dict(draft=draft, context=context, accept_unverified=accept_unverified,
                                                    version_id=version["id"],
                                                    **(dict(answered=version["answered"]) if version.get("answered") else {})),
                                        state="received" if self.committer else "effective")
                notices.settle(self.store, owner, prior) if self.committer is None else None
        timeout = self.commit_wait if wait is None else wait
        if self.committer is not None and prior["state"] == "received" and timeout <= 0:
            self.committer.poke()  # the background loop commits and pushes it after its batch window
        if self.committer is not None and prior["state"] == "received" and timeout > 0:
            self.committer.wait(prior["id"], timeout)
            # Replayed on a newer head while we waited: the version as it now stands, with `redone` when it was redone (D-75).
            try:
                version = self.store.get("versions", owner, version["id"])
            except ValueError:
                pass
        # The user locks this commit changed, from the operations the ledger admitted and applied in it (D-49).
        draft = self.store.get("drafts", owner, draft_id)
        locks = draft.get("committed_lock_changes")
        # Saved with unverified facts the user did not answer about (the proactive mode): the answer the agent writes
        # next ends with how to undo it. The receipt is the last thing it reads before writing (2026-11-13: the same
        # sentence in propose's tell_user was not said in 2 runs of 2).
        undo = self._saved_say(owner, draft, version, ask_first)
        ids = {item.get("id") for item in (draft.get("changes") or {}).get("items", [])}
        ineffective = self._settings_not_in_effect(owner, [item for item in self.store.all("items", owner) if item["id"] in ids],
                                                   self._reply_language(owner)) if ids else []
        return dict(version, accepted=True, write=self.write_ref(owner, prior), **(dict(lock_changes=locks) if locks else {}),
                    **(dict(tell_user=undo) if undo else {}), **(dict(settings_not_in_effect=ineffective) if ineffective else {}))

    def _saved_say(self, owner: str, draft: dict, version: dict, ask_first: bool, *, replay: bool = False) -> dict | None:
        """The sentence the answer ends with after this commit (tell_user.say), or None. An undo: the version it went back
        to, the older records still in force that the restored plan no longer meets, and the first time this user undoes a
        change in the proactive mode, the cautious mode offered once (`replay`: not again). Any other plan change saved in
        the proactive mode: the mode, the page being updated, and “撤回” to undo it. Nothing for a ledger-only commit, for
        the cautious mode (the user saw the change before it was saved) or an answered ask."""
        if ledger_only(draft) or version.get("answered"):
            return None
        proactive = not ask_first and not self.asks_first(owner)
        language = "en" if self._reply_language(owner) == "en" else "zh"
        page = "page" if self._page_connected() else "plain"
        restored = version.get("restored_from")
        if restored is not None:
            words = RESTORED_SAY[language]
            earlier = [entry for entry in self.store.all("versions", owner)
                       if entry.get("restored_from") is not None and entry["id"] != version["id"]]
            offer = proactive and not earlier and not replay and consider.save_mode(self.store, owner, at=now())["by"] != "user"
            taken = self._taken_back(owner, draft)
            gap = " " if language == "en" else ""
            say = words[page].format(restored=restored)
            if taken:
                # round 4, 2.1: the entries the undone turns recorded went with the undo; the answer names each one, so
                # the user can say which to keep instead of being asked before the undo.
                say += gap + words["taken"].format(what=words["join"].join(entry["text"].rstrip("。.；;，, ") for entry in taken))
                say += gap + words["keep"]
            gone = self._bookings_gone(owner, draft, version)
            if gone:
                # the boundary the design names: a ticket bought in those turns is not the service's to cancel
                say += gap + words["tickets"].format(what=words["join"].join(gone))
            still = self._still_in_force(owner, version, restored)
            if still:
                # round 4 review: a decision point must not go silent. A record older than the undone turns stays in
                # force and can now disagree with the restored plan; the undo still goes through and asks nothing.
                say += gap + gap.join(words["still"].format(what=what) for what in still)
            say += gap + words["offer"] if offer else ""
            return dict(say=say, how=SAY_HOW[language], **({"taken_back": taken} if taken else {}),
                        **({"offer": "cautious_mode"} if offer else {}))
        if not proactive:
            return None
        return dict(say=PROACTIVE_SAY[language][page], how=SAY_HOW[language], restore_version=version["base_version"])

    def _bookings_gone(self, owner: str, draft: dict, version: dict) -> list[str]:
        """The bought or booked arrangements the version this undo goes back to did not have: the record of them went
        with the undo, but the ticket itself is the user's to cancel or keep (round 4, 2.1)."""
        before = next((entry for entry in self.store.all("versions", owner) if entry["number"] == draft.get("base_version")), None)
        if before is None:
            return []
        now = {booking["id"] for booking in (version.get("plan") or {}).get("bookings", [])}
        return [f"{booking.get('what') or booking['id']}（{booking.get('date')}）" if self._reply_language(owner) != "en"
                else f"{booking.get('what') or booking['id']} ({booking.get('date')})"
                for booking in (before.get("plan") or {}).get("bookings", [])
                if booking["id"] not in now and booking.get("status") in ("bought", "booked")]

    def _still_in_force(self, owner: str, version: dict, restored: int) -> list[str]:
        """The records older than the undone turns that stay active while the restored plan no longer meets them (round 4
        review: an undo must not silence a decision point). A must decision is compared by its locks the way the lock check
        compares them; a must item by the times its own words state, against the plan's block of the same kind. Only what
        can be told for certain is named: the undo still goes through and nothing is asked."""
        from tripwright.definite import BLOCK_KINDS, objects_named
        from tripwright.ledger import lock_blocks, record_values
        from tripwright.locks import binding, lock_changes, stable_path

        plan = version.get("plan") or {}
        if not plan:
            return []
        undone = self._undone_events(owner, restored, besides=version.get("id"))

        def older(record: dict) -> bool:
            return record.get("status") == "active" and (record.get("source") or {}).get("event") not in undone

        blocks = lock_blocks(plan)
        found = []
        for decision in self.store.all("decisions", owner):
            if not decision.get("must") or not older(decision):
                continue
            for path, expected in (decision.get("locked_paths") or {}).items():
                if not binding(decision, path):
                    continue
                stable = stable_path(plan, path)
                if lock_changes(plan, stable, expected, (decision.get("placement") or {}).get(stable)):
                    found.append(f"{decision.get('question')}：{decision.get('choice')}")
                    break
        for item in self.store.all("items", owner):
            if not older(item) or item.get("strength") != "must":
                continue
            said = {value for field, value in record_values("items", item) if field == "time"}
            kinds = objects_named(" ".join(str(item.get(key) or "") for key in ("key", "text", "quote")))
            matching = [block for block in blocks.values() if BLOCK_KINDS.get(block.get("kind")) in kinds]
            if not said or not matching:  # its object or its time cannot be read from the plan: nothing to claim
                continue
            clocks = {str(block.get(edge) or "")[:5] for block in matching for edge in ("start", "end")}
            if not said & clocks:
                found.append(item.get("text") or item.get("key"))
        return found

    def _taken_back(self, owner: str, draft: dict) -> list[dict]:
        """What this commit withdrew (changes.withdraw), each with the words the answer names it by."""
        found = []
        for entry in (draft.get("changes") or {}).get("withdraw") or []:
            for table in ("decisions", "items", "facts"):
                try:
                    record = self.store.get(table, owner, entry["id"])
                except ValueError:
                    continue
                text = f"{record['question']}：{record['choice']}" if table == "decisions" else record.get("text") or record["id"]
                found.append(dict(id=record["id"], text=text, reason=entry.get("reason")))
                break
        return found

    def _page_connected(self) -> bool:
        """A public page is connected: the committer's publisher rebuilds it in the background after each new version."""
        if self.committer is None:
            return False
        from tripwright.site.publish import share_connection

        return share_connection(self.committer.publisher.data_dir) is not None

    def result(self, owner: str, job_id: str, *, cancel: bool = False, ack_events: list[str] | None = None,
               report: dict | None = None) -> dict:
        self.bound(owner)
        # result keeps its contract's exact shape (CONTRACTS 1.4): no flow line here
        if cancel:
            return self.jobs.cancel(owner, job_id)
        response = self._job_result(owner, job_id, ack_events)
        return self._verifier_report(owner, response, report) if report is not None else response

    def _verifier_report(self, owner: str, response: dict, report: dict) -> dict:
        """The verifier's own verdict, handed back with `result(job_id, report=...)` (analysis 5.14, 2026-11-13). Before,
        it reached only the main agent: reading the wide pack counted as checked, and a draft the verifier found
        incomplete was saved 2 times in 3 with nothing of its verdict on the version. Recorded on the draft only for a
        completed, fresh result; commit then follows it (versions.commit): a conflict is refused, an incomplete check
        waits for the user in the cautious mode and is saved with its unchecked lines in the proactive one. A conflict
        stays on its draft: a later report on the same draft does not replace it."""
        checked = verifier_report(report)
        if response.get("status") != "completed" or not response.get("result"):
            return dict(response, notice=response.get("notice") or f"Report not recorded: the result is {response.get('status')}")
        draft_id = response["result"]["draft_id"]
        language = self._reply_language(owner)
        with self.store.transaction():
            draft = self.store.get("drafts", owner, draft_id)
            earlier = draft.get("verifier_report") or {}
            if earlier.get("verdict") == "conflict" and checked["verdict"] != "conflict":
                # a conflict stays on this draft (fix3-judge V1b, 2026-11-13: a second report saying passed let the same
                # draft be saved): to save, change the draft and propose again, which gets its own check
                kept = {key: earlier[key] for key in ("verdict", "unchecked", "conflicts")}
                return dict(response, result=dict(response["result"], report=dict(
                    kept, recorded=False, next=REPORT_NEXT["en" if language == "en" else "zh"]["conflict"])),
                    notice="Report not recorded: this draft already has the verifier's conflict, which stays; change the draft and propose again")
            self.store.put("drafts", owner, dict(draft, verifier_report=dict(checked, at=now(), job_id=response["job_id"])))
        self.flow.verified(owner)
        cautious = self.asks_first(owner)
        say = REPORT_NEXT["en" if language == "en" else "zh"][
            checked["verdict"] if checked["verdict"] != "incomplete" or not checked["unchecked"] else ("ask" if cautious else "tell")]
        return dict(response, result=dict(response["result"], report=dict(checked, recorded=True, next=say)))

    def _job_result(self, owner: str, job_id: str, ack_events: list[str] | None) -> dict:
        return self.jobs.result(owner, job_id, ack_events=ack_events, body=lambda job, message: self._verdict(owner, job, message))

    def _verdict(self, owner: str, job: dict, message: dict) -> dict:
        """CONTRACTS 1.4: passed only without conflicts, unverified critical facts and unaddressed dispositions, and with
        every group of the wide pack complete; it says nothing about the answer the host writes."""
        if job["kind"] != "deep_check":
            return {}
        value = message["value"]
        checks = value.get("checks", {})
        usages = checks.get("usages", [])
        draft = self.store.get("drafts", owner, value["draft_id"])
        groups = ((draft.get("verify") or {}).get("meta") or {}).get("required", {}).values()
        if checks.get("conflicts"):
            verdict = "conflict"
        elif any(check.get("critical") for check in checks.get("unverified", [])) or any(
                usage.get("status") in ("unaddressed", "invalid") for usage in usages) or any(group["status"] != "complete" for group in groups):
            verdict = "incomplete"
        else:
            verdict = "passed"
        return dict(draft_id=value["draft_id"], verdict=verdict, usages=usages,
                    conflicts=[check["id"] for check in checks.get("conflicts", [])],
                    unverified=[check["id"] for check in checks.get("unverified", [])])

    def state(self, owner: str, view: str = "current", date: str | None = None, draft_id: str | None = None,
              role: str | None = None) -> dict:
        """Every view carries `now` (A2): what is current is read against the moment of the read."""
        self.bound(owner)
        if view == "role":
            # A role's steps for a host without the skill files (G1, 2026-11-13): the same text the skill ships.
            if not role:
                raise ValueError("view='role' needs role, one of: " + ", ".join(agents.ROLES))
            return self._flowed(owner, "state_role", dict(agents.role_steps(role), now=self.clock(owner)))
        return self._flowed(owner, "state_pack" if view == "pack" else "state",
                            dict(self._state(owner, view, date, draft_id), now=self.clock(owner)))

    def _state(self, owner: str, view: str, date: str | None, draft_id: str | None) -> dict:
        current = self.versions.current(owner)
        if view in ("current", "plan"):
            return current
        if view == "blank":
            blank = Trip(id="new-trip", title="New trip", start="2030-01-01", end="2030-01-01", timezone="UTC",
                         areas=["area"], people=[dict(id="p1", name="Traveler")])
            return dict(plan=blank.model_dump(mode="json"))
        if view == "day":
            # The text view remains; json is the same day object that propose(days=[...]) accepts.
            text = repo_files.day_text(self, owner, date or "")
            day = next((day for day in (current["plan"] or {}).get("days", []) if str(day["date"]) == date), None)
            if day is None:
                raise ValueError(f"Unknown plan day: {date!r}")
            return dict(version=current["number"], needs_replan=current.get("needs_replan", False),
                        warnings=current.get("warnings", []), text=text, json=day)
        if view == "ledger":
            return self.ledger.current(owner)
        if view == "profile":
            return dict(profile=self.ledger.profile(owner), preferences=[item for item in self.ledger.current(owner)["items"] if item["kind"] == "preference"],
                        memory=memory.memory_data(self.store, owner, trip=self.current_trip(owner), at=now()))
        if view == "memory":
            return dict(memory.memory_data(self.store, owner, trip=self.current_trip(owner), at=now()), **self._memory_all(owner))
        if view == "pack":
            if not draft_id:
                raise ValueError("view='pack' needs draft_id")
            draft = self.store.get("drafts", owner, draft_id)
            verify = draft.get("verify") or {}
            return dict(draft_id=draft_id, job_id=draft.get("job_id"), markdown=verify.get("markdown"), meta=verify.get("meta"),
                        candidate=draft["plan"], checks=draft["checks"], changes=draft["changes"],
                        stale=self.versions.fresh(owner, draft.get("snapshot")))
        if view == "sync":
            sync = self.committer.status() if self.committer else dict(canonical=None, unsynced=0, note="single-database mode")
            # site/publish.py records every attempt: where each trip's public page is, or why it is not there
            pages = [{key: item[key] for key in ("trip", "status", "url", "plan_version", "blocked", "error") if item.get(key) is not None}
                     for item in self.store.all("share", owner) if item.get("status") != "superseded"]
            return dict(sync, public_pages=pages) if pages else sync
        if view == "decisions":
            return self.ledger.explain(owner)
        if view == "graph":
            return self.ledger.graph(owner)
        if view == "history":
            # D-53: what each version changed, field by field, who changed it, on what basis and why.
            return dict(versions=[dict(version=version["number"], committed_at=version["committed_at"], source_event=version.get("source_event"),
                                       origin=version.get("origin"), changelog=version.get("changelog"),
                                       **({"restored_from": version["restored_from"]} if version.get("restored_from") is not None else {}))
                                  for version in sorted(self.store.all("versions", owner), key=lambda version: version["number"])])
        if view == "export":
            return {"format": "tripwright-state-v1", "version": current, "ledger": self.ledger.current(owner),
                    "records": {table: self.store.all(table, owner) for table in ("events", "items", "facts", "decisions", "pending_checks", "journal")},
                    "notice": "For a round-trip directory export including jobs and history, use the local export CLI."}
        raise ValueError("Unknown state view")

    def _memory_all(self, owner: str) -> dict:
        """Everything turn's consider can list, for the whole trip and everyone (M1): `consider` and `sources`."""
        at = now()
        plan = self.versions.current(owner)["plan"]
        language = self._reply_language(owner)
        listed = consider.build(self.store, owner, pack=None, plan=plan, scenario="plan", at=at, language=language, limit=None)
        return dict(consider=listed["entries"], sources=consider.sources(self.store, owner, plan, at=at, language=language))

    def explain(self, owner: str, record_id: str | None = None, version: int | None = None) -> dict:
        return self._flowed(owner, "explain", self._explain_tool(owner, record_id, version))

    def _explain_tool(self, owner: str, record_id: str | None = None, version: int | None = None) -> dict:
        self.bound(owner)
        explanation = self.ledger.explain(owner, record_id)
        versions = self.store.all("versions", owner)
        if version is not None:
            versions = [candidate for candidate in versions if candidate["number"] == version]
            if not versions:
                raise ValueError("Unknown version")

        def constraints(candidate):
            # Contexts are runtime state: a version rebuilt from the repository or adopted from a hand edit has none.
            try:
                return self.store.get("contexts", owner, candidate["ctx_id"])["bundle"] if candidate.get("ctx_id") else None
            except ValueError:
                return None

        card = self._record_card(owner, record_id) if record_id else None
        if card is None:
            explanation["version_usage"] = [dict(version=candidate["number"], ctx_id=candidate.get("ctx_id"), usages=candidate.get("usages", []),
                                                  origin=candidate.get("origin"), constraints=constraints(candidate),
                                                  changelog=candidate.get("changelog"))
                                            for candidate in versions]
            return explanation
        # S5 (METHOD 2.9 second level): one record by its ID, also over MCP alone: the record itself and, unless a
        # version is asked for, only the latest version, with this record's usage, whether its pack carried it and the
        # change-log lines that name it.
        if version is None:
            versions = sorted(versions, key=lambda candidate: candidate["number"])[-1:]
        explanation["record"] = card
        explanation["version_usage"] = [dict(version=candidate["number"], ctx_id=candidate.get("ctx_id"), origin=candidate.get("origin"),
                                              usages=[usage for usage in candidate.get("usages", []) if usage.get("item_id") == record_id],
                                              carried=record_id in json.dumps(constraints(candidate) or {}),
                                              changelog=[line for line in candidate.get("changelog") or [] if record_id in json.dumps(line)])
                                        for candidate in versions]
        return explanation

    def _record_card(self, owner: str, record_id: str) -> dict | None:
        """One ledger record for explain (S5): its words, whose it is (consider's layer words), the message it comes from
        (packs.message: also an imported long-term entry's), its status, what replaced it and the record in effect now,
        its relations and the agent's entries that give way to it. None for an ID that is no ledger record."""
        for table, name in RECORD_TABLES:
            if self._has(table, owner, record_id):
                found = self.store.get(table, owner, record_id)
                break
        else:
            return None
        events = {event["id"]: event for event in self.store.all("events", owner) if "received" in event}
        basis = (found.get("source") or {}).get("basis")
        code = ("user" if found["decided_by"] == "user" else "agent") if name == "decision" else consider.basis_layer(basis) if basis else None
        items = self.store.all("items", owner) if name == "item" else []
        replaced_by = ([item["id"] for item in items if record_id in item.get("relations", {}).get("supersedes", [])] if name == "item" else
                       [found["superseded_by"]] if found.get("superseded_by") else [])
        current, seen = record_id, {record_id}
        while True:  # the end of the replacement chain: the record in effect now
            following = ([item["id"] for item in items if current in item.get("relations", {}).get("supersedes", [])] if name == "item" else
                         [self.store.get(table, owner, current).get("superseded_by")] if name == "decision" else [])
            following = [entry for entry in following if entry and entry not in seen]
            if not following:
                break
            current = following[-1]
            seen.add(current)
        text = (f"{found['question']} → {found['choice']}" if name == "decision" else found.get("text") or found.get("question"))
        card = dict(id=record_id, table=name, status=found["status"], text=text, quote=found.get("quote"), basis=basis,
                    layer=consider.words(self._reply_language(owner))["layer"][code] if code else None, said=packs.message(found, events),
                    subject=found.get("subject"), scope=packs.bare_scope(found.get("scope")), superseded_by=replaced_by,
                    current=current, relations=found.get("relations") or {})
        card.update({key: found[key] for key in RECORD_FIELDS[name] if found.get(key) is not None})
        conflicted = [item["id"] for item in items if record_id in item.get("relations", {}).get(consider.CONFLICTS_USER, [])]
        if conflicted:
            card["conflicted_by"] = conflicted
        return card
