"""Edits made in the working copy without the tool (design 5.6), looked at before every commit.

- A changed plan (`trip.yaml`, `plan/**`) is a proposal: if every check passes and no user-decided part moved, it
  becomes a new version recorded as an external edit; otherwise the version stays, the edit stays in the working copy
  (not overwritten) and the front screen says "有未入版的手改：冲突 Y".
- A changed generated file is overwritten by the next commit, which says so once.
- A changed message file is restored (messages never change) and said once; a record file that no longer parses keeps
  its previous content in the index, stays as the user left it, and is reported until fixed.
"""
from __future__ import annotations

from tripwright.repo import index, layout, notices
from tripwright.repo.git import GitError


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
        if plan_paths:
            outcome["plan"] = _plan(service, owner, trip, folder, plan_paths)
        else:
            notices.close(store, owner, key="manual:plan")
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


def _plan(service, owner: str, trip: str, folder: str, paths: list[str]) -> dict:
    store, versions = service.store, service.versions
    files = index.trip_part(service.repository.working(folder + "/"), trip)
    try:
        plan = layout.plan_from(files)
    except ValueError as error:
        service.held_paths.update(paths)
        notices.manual(store, owner, key="manual:plan", user_line="有未入版的手改：计划文件格式坏了，版本没变。",
                       detail=str(error), paths=paths)
        return dict(adopted=False, reason=str(error))
    current = versions.current(owner)
    if plan is None or (current["plan"] is not None and layout.plan_digest(plan) == layout.plan_digest(current["plan"])):
        notices.close(store, owner, key="manual:plan")
        return dict(adopted=False, reason="no change")
    try:
        author = service.repository.git.run("config", "user.name").strip() or "external"
    except GitError:
        author = "external"
    try:
        result = versions.adopt(owner, plan, author=f"external edit ({author})", base_version_id=current["id"])
    except ValueError as error:
        result = dict(adopted=False, conflicts=[str(error)])
    if not result["adopted"]:
        service.held_paths.update(paths)
        notices.close(store, owner, key="manual:plan")
        notices.manual(store, owner, key="manual:plan", user_line="有未入版的手改：冲突 " + "；".join(result["conflicts"]),
                       detail="The edited plan failed the checks; the version is unchanged and the edit stays in the working copy.",
                       paths=paths)
        return result
    notices.close(store, owner, key="manual:plan")
    version = result["version"]
    service.outbox.add(owner, kind="manual", tool="manual", key=version["id"], version_id=version["id"],
                       replay=dict(plan=plan, base_version_id=current["id"], author=version["submitted_by"], version_id=version["id"]))
    return dict(adopted=True, version_id=version["id"], number=version["number"])
