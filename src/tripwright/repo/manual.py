"""Edits made to the trip's files without the tool (design 5.6), taken in before the tool writes over them.

They are looked at when a tenant opens, after a sync brings a new head, before every write and before every commit.
- A changed plan (`trip.yaml`, `plan/**`) is a proposal. It is found by comparing the plan files with the version the
  files last recorded (the last line of `plan/versions.jsonl` in the working copy), so an edit that is already in a
  commit (made on the Git host's web page, by another agent, or committed by hand) counts as well as one only in the
  working copy (A15). If every check passes and no user-decided part moved, it becomes a new version recorded as an
  external edit; when the tool made a newer version meanwhile, the edit is redone on it object by object (repo.rebase),
  and a field both changed differently is not taken in. Otherwise the version stays, the edit stays in the files (not
  overwritten) and the front screen says "有未入版的手改：…".
- A changed generated file is overwritten by the next commit, which says so once.
- A changed message file is restored (messages never change) and said once; a record file that no longer parses keeps
  its previous content in the index, stays as the user left it, and is reported until fixed.
"""
from __future__ import annotations

from tripwright.repo import layout, notices, rebase
from tripwright.repo.git import GitError


KEPT = "版本没变，手改留在文件里；改好或撤掉后，下次写入时收进来。"


def adopt(service) -> dict:
    repository, owner, store = service.repository, service.repository.owner, service.store
    service.held_paths = set()
    trip = service.current_trip(owner)
    if not trip:
        return {}
    folder = layout.trip_folder(trip)
    try:
        changed = repository.git.changed((folder, "README.md", layout.PROFILE))
    except GitError:
        return {}
    new = {path for path, status in changed.items() if status in ("??", "A ")}
    plan_paths = sorted(path for path in changed if path == f"{folder}/trip.yaml"
                        or (path.startswith(f"{folder}/plan/") and path != f"{folder}/plan/versions.jsonl"))
    generated = sorted(path for path in changed if path in ("README.md", f"{folder}/README.md") or path.startswith(f"{folder}/views/"))
    messages = sorted(path for path in changed if path.startswith(f"{folder}/raw/") and path not in new)
    records = sorted(path for path in changed if path not in new and path not in plan_paths and path not in generated
                     and path not in messages and not path.startswith(f"{folder}/research/") and path != "profile/materials.yaml")
    outcome = dict(plan=None, generated=generated, messages=messages, records=[])
    with store.transaction():
        outcome["plan"] = _plan(service, owner, trip, folder)
        if generated:
            notices.manual(store, owner, key="generated:" + ",".join(generated), user_line="生成的文件被手改过，这次提交已按记录重新生成。",
                           detail="Generated files are rewritten from the records; edit the records through the tool.", paths=generated, once=True)
        if messages:
            notices.manual(store, owner, key="messages:" + ",".join(messages), user_line="原话文件被改过，已恢复原样（原话建了就不改）。",
                           detail="Message files are never edited; the committed text was restored.", paths=messages, once=True)
        working = repository.working(folder + "/") | repository.working(layout.PROFILE + "/")
        for path in records:
            if path not in working:
                continue
            try:
                layout.parse(path, working[path])
            except ValueError as error:
                service.held_paths.add(path)
                notices.manual(store, owner, key=f"format:{path}", user_line=f"文件格式坏了：{path}；索引用上一个有效内容。",
                               detail=str(error), paths=[path])
                outcome["records"].append(path)
                continue
            notices.close(store, owner, key=f"format:{path}")
            notices.manual(store, owner, key=f"record:{path}", user_line=f"记录被手改，没有收进账，已按账恢复：{path}。改记录请经工具。",
                           detail="Records change only through the tool; the committed content was restored.", paths=[path], once=True)
            outcome["records"].append(path)
    return outcome


def _plan(service, owner: str, trip: str, folder: str) -> dict:
    store, versions, repository = service.store, service.versions, service.repository
    files = {path[len(folder) + 1:]: text for path, text in repository.working(folder + "/plan/").items()}
    if (repository.root / folder / "trip.yaml").is_file():
        files["trip.yaml"] = (repository.root / folder / "trip.yaml").read_text(encoding="utf-8")
    current = versions.current(owner)
    base = _base(store, owner, files, current)
    expected = layout.plan_files(base["plan"] if base else None, trip)
    paths = sorted(f"{folder}/{path}" for path in set(files) | set(expected)
                   if path != "plan/versions.jsonl" and files.get(path) != expected.get(path))
    if not paths:
        notices.close(store, owner, key="manual:plan")
        return dict(adopted=False, reason="no change")
    try:
        plan = layout.plan_from(files)
    except ValueError as error:
        return _held(service, owner, paths, base, f"有未入版的手改：计划文件格式坏了（{_names(paths, folder)}）。{KEPT}", str(error))
    if plan is None:
        notices.close(store, owner, key="manual:plan")
        return dict(adopted=False, reason="no change")
    candidate = plan
    if base and base["id"] != current["id"] and base["plan"] is not None and current["plan"] is not None:
        # The tool made a newer version after the files were written: the hand edit is redone on it (D-75).
        merged, clashes = rebase.onto(base["plan"], plan, current["plan"])
        if clashes:
            return _held(service, owner, paths, base, "有未入版的手改：" + rebase.user_line(clashes, current["plan"]) + KEPT,
                         "The hand edit and a later version changed the same item differently; the version is unchanged "
                         "and the edit stays in the files.")
        candidate = merged
    if current["plan"] is not None and layout.plan_digest(rebase.normal(candidate)) == layout.plan_digest(current["plan"]):
        notices.close(store, owner, key="manual:plan")
        return dict(adopted=False, reason="no change")
    try:
        author = repository.git.run("config", "user.name").strip() or "external"
    except GitError:
        author = "external"
    try:
        result = versions.adopt(owner, candidate, author=f"external edit ({author})", base_version_id=current["id"])
    except ValueError as error:
        return _held(service, owner, paths, base, f"有未入版的手改：{_names(paths, folder)} 不合计划的格式：{_reasons(error, candidate)}。{KEPT}",
                     "The edited plan is not a valid trip document; the version is unchanged and the edit stays in the files.")
    if not result["adopted"]:
        return _held(service, owner, paths, base, f"有未入版的手改：冲突 {'；'.join(result['conflicts'])}（{_names(paths, folder)}）。{KEPT}",
                     "The edited plan failed the checks; the version is unchanged and the edit stays in the files.")
    notices.close(store, owner, key="manual:plan")
    version = result["version"]
    service.outbox.add(owner, kind="manual", tool="manual", key=version["id"], version_id=version["id"],
                       replay=dict(plan=candidate, base_version_id=current["id"], author=version["submitted_by"], version_id=version["id"]))
    return dict(adopted=True, version_id=version["id"], number=version["number"])


def _base(store, owner: str, files: dict, current: dict) -> dict | None:
    """The version the plan files were edited from: the one a held edit was first seen on, else the last version the
    files recorded (the tool may hold newer ones it has not written yet), else none when no version was written."""
    for notice in store.all("notices", owner):
        if notice.get("key") == "manual:plan" and notice["status"] == "open" and notice.get("base_version_id"):
            try:
                return store.get("versions", owner, notice["base_version_id"])
            except ValueError:
                break
    try:
        lines = layout.parse("plan/versions.jsonl", files["plan/versions.jsonl"]) if "plan/versions.jsonl" in files else []
    except ValueError:
        return current
    if not lines:
        return None
    last = max(lines, key=lambda line: line["number"])
    try:
        return store.get("versions", owner, last["id"])
    except ValueError:
        return current


def _held(service, owner: str, paths: list[str], base: dict | None, user_line: str, detail: str) -> dict:
    """The edit is not taken in: its files are not overwritten, and an open notice says so until it is fixed or undone."""
    store = service.store
    service.held_paths.update(paths)
    open_notice = next((notice for notice in store.all("notices", owner)
                        if notice.get("key") == "manual:plan" and notice["status"] == "open"), None)
    if open_notice is None or open_notice["user_line"] != user_line:
        notices.close(store, owner, key="manual:plan")
        notice = notices.manual(store, owner, key="manual:plan", user_line=user_line, detail=detail, paths=paths)
        notice["base_version_id"] = (open_notice or {}).get("base_version_id") or (base or {}).get("id")
        store.put("notices", owner, notice)
    return dict(adopted=False, conflicts=[user_line])


def _names(paths: list[str], folder: str) -> str:
    names = [path[len(folder) + 1:] for path in paths]
    return "、".join(names[:3]) + (f" 等 {len(names)} 个文件" if len(names) > 3 else "")


def _reasons(error: ValueError, plan: dict) -> str:
    """A model error in a line: which day and object of the plan, and why, without the input it echoes."""
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return str(error).splitlines()[0][:200]
    lines = []
    for item in errors()[:3]:
        where, node = [], plan
        for part in item.get("loc", ()):
            try:
                node = node[part]
            except (KeyError, IndexError, TypeError):
                node = None
            if isinstance(node, dict) and (node.get("date") or node.get("id")):
                where.append(str(node.get("date") or node.get("id")))
            elif isinstance(part, str) and not isinstance(node, (dict, list)):
                where.append(part)
        lines.append(f"{' '.join(where) or 'trip.yaml'}：{str(item.get('msg', '')).removeprefix('Value error, ')}")
    return "；".join(lines)
