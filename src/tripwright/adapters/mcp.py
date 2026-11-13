from __future__ import annotations

import asyncio
import logging
import os
import threading
import inspect
from contextlib import asynccontextmanager
from contextvars import ContextVar
from importlib.metadata import version
from functools import wraps
from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal

import pydantic_core
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError, WithJsonSchema

from tripwright.agents import server_instructions
from tripwright.events import SourceNature
from tripwright.trip import input_problems

if TYPE_CHECKING:
    from tripwright.service import Tripwright

ITEM_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "required": ["kind", "key", "quote", "text", "subject", "strength", "source"],
    "properties": {
        "kind": {"type": "string", "enum": ["goal", "constraint", "preference", "rule", "authorization", "stop", "setting"]},
        "key": {"type": "string"}, "text": {"type": "string"},
        "quote": {"type": "string", "description": "Copied character for character from the message text"},
        "subject": {"type": "array", "items": {"type": "string"},
                    "description": "The trip's people it applies to (IDs or names, ['*'] for all); never a place"},
        "strength": {"type": "string", "enum": ["must", "forbid", "prefer", "optional"]},
        "source": {"type": "object", "additionalProperties": True, "required": ["event"], "properties": {
            "event": {"type": "string"},
            "basis": {"type": "string", "enum": ["user_explicit", "direction_set", "recommended_default", "agent_inference"]},
        }},
    },
}
CHANGES_SCHEMA = {"type": "object", "additionalProperties": True, "properties": {
    "items": {"type": "array", "items": ITEM_SCHEMA},
}}
# JSON Schema guides the host; the service validates values so SDK/pydantic dumps never leak to the user.
RecordArguments = Annotated[Any, WithJsonSchema(CHANGES_SCHEMA)]
PlanArguments = Annotated[Any, WithJsonSchema({"type": "object"})]
ObjectList = Annotated[Any, WithJsonSchema({"type": "array", "items": {"type": "object"}})]
UsageArguments = Annotated[Any, WithJsonSchema({
    "type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["item_id", "status"],
        "properties": {
            "item_id": {"type": "string"},
            "status": {"type": "string", "enum": ["used", "host_reported", "traded_off", "not_applicable"]},
            "block_ids": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
    },
})]


TURN_DESCRIPTION = (
    "Receives one user message about a trip and returns what the service holds for it. The service sees only what "
    "arrives here: a trip message that does not pass through turn leaves no record, and none of the stored "
    "requirements, decisions or plan details come back for it; propose takes the ctx_id and base_version it returns. "
    "Returns the stored requirements and decisions that apply (pack, consider), the current time, and next and flow: "
    "the steps for this message. For a quick question on the road (scenario='quick', with the message's points) it "
    "also returns first_part, an answer drafted from the stored plan with unchecked times marked, and "
    "second_part_title, the heading for the answer after lookups. record stores what the message states "
    "(preferences, facts, decisions) with the user's exact words. A message that only continues an unfinished second "
    "part needs no new turn.\n"
    "Args: points={kind:at|go|eat|do|ticket|other,said,place?}[]; record={items,facts,relate,carry,pending_checks}, "
    "source.event='this'; request_id optional.")

request_user: ContextVar[str | None] = ContextVar("tripwright_user", default=None)
log = logging.getLogger("tripwright.tools")


def unexpected(name: str, error: BaseException) -> ToolError:
    """An error the tool did not expect: one log line for the operator (with the traceback at debug level), and a
    refusal that tells the host what kind of failure it was and what to do, instead of the SDK's bare "Error executing
    tool"."""
    log.error("tool %s failed: %s: %s", name, type(error).__name__, error)
    log.debug("tool %s traceback", name, exc_info=error)
    return ToolError(f"Unexpected server error in {name} ({type(error).__name__}); retry once, and if it fails again "
                     "tell the user the server operator must check its log")


def compact(result):
    """A dict result as compact JSON text beside the same structured content. The library indents the text by two
    spaces, about 29% of a `turn` result's bytes (2026-11-12); the text is what a model reads."""
    if not isinstance(result, dict):
        return result
    text = pydantic_core.to_json(result, fallback=str).decode()
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=pydantic_core.from_json(text))


def expose_errors(function):
    def reason(error):
        if isinstance(error, ValidationError):
            root = "changes" if function.__name__ == "propose" else "arguments"
            return f"Invalid {root}: " + "; ".join(input_problems(error, root))
        if type(error) is KeyError:
            return f"Missing required argument: {error.args[0]}"
        return str(error.args[0]) if isinstance(error, LookupError) and error.args else str(error)

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(*args, **kwargs):
            try:
                return compact(await function(*args, **kwargs))
            except (ValueError, KeyError, TypeError) as error:
                raise ToolError(reason(error)) from None
            except ToolError:
                raise
            except Exception as error:
                raise unexpected(function.__name__, error) from None
        return asynchronous

    @wraps(function)
    def synchronous(*args, **kwargs):
        try:
            return compact(function(*args, **kwargs))
        except (ValueError, KeyError, TypeError) as error:
            raise ToolError(reason(error)) from None
        except ToolError:
            raise
        except Exception as error:
            raise unexpected(function.__name__, error) from None
    return synchronous


class Opening:
    """The service behind a stdio server, opened after the server starts (A2). A new clone has no index, so opening it
    reads the whole repository; a host that waits for the handshake must not wait for that, and a repository that
    cannot be opened must not keep the host from connecting. Each tool call waits until the service is open; if opening
    failed, every call is refused with the reason, so the host knows the tools are there and why they cannot answer."""

    def __init__(self, open_service: Callable[[], "Tripwright"] | None = None, *, service: "Tripwright | None" = None,
                 wait: float = 120.0):
        self.open_service = open_service
        self.service = service
        self.error: str | None = None
        self.wait = wait
        self.done = threading.Event()
        self.thread: threading.Thread | None = None
        self.background = False
        self.stopped = False
        if service is not None:
            self.done.set()

    def start(self, *, background: bool):
        """Open (in a thread when not open yet) and then start the service's background workers when asked."""
        self.background = background
        if self.done.is_set():
            self._started()
            return

        def work():
            try:
                self.service = self.open_service()
                self._started()
            except Exception as error:  # every failure becomes the tools' refusal reason
                self.error = f"{type(error).__name__}: {error}"
            finally:
                self.done.set()

        self.thread = threading.Thread(target=work, name="tripwright-open", daemon=True)
        self.thread.start()

    def _started(self):
        if self.background and self.service is not None:
            self.service.jobs.start()
            if self.service.committer is not None:
                self.service.committer.start()

    def get(self) -> "Tripwright":
        if not self.done.wait(self.wait):
            raise ValueError(f"Tripwright is still opening the trip repository after {self.wait:.0f} s; try again")
        if self.service is None:
            raise ValueError(f"Tripwright could not open the trip repository, so its tools cannot answer in this session: {self.error}")
        return self.service

    async def get_async(self) -> "Tripwright":
        return self.get() if self.done.is_set() else await asyncio.to_thread(self.get)

    def stop(self):
        """Stop the background workers once; an opening still running at shutdown gets a few seconds to finish."""
        if self.thread is not None:
            self.thread.join(timeout=10)
        if self.background and self.service is not None and not self.stopped:
            self.stopped = True
            self.service.jobs.stop()
            if self.service.committer is not None:
                self.service.committer.stop()


def create_server(service: "Tripwright | Opening", *, local_user: str | None = "local", manage_jobs: bool = True) -> MCPServer:
    """`service` is an open service, or an `Opening` that opens it once the server runs (the stdio command)."""
    opening = service if isinstance(service, Opening) else Opening(service=service)
    ready = opening.get

    @asynccontextmanager
    async def lifespan(server):
        opening.start(background=manage_jobs)
        try:
            yield {}
        finally:
            opening.stop()

    # Tool descriptions say what a tool does and returns; they give the model no orders. ChatGPT's web app marked
    # the earlier ones "suspicious instructions: the tool description tries to set risk labels, approval and output
    # format and asks to run its flow first" (2026-11-12): turn's description then repeated the six chat rules, and
    # commit's told the model when it may set accept_unverified. What a host does for a message is said in each
    # result (next, flow), and what must hold is checked by the service (commit's refusals).
    server = MCPServer("tripwright", title="Tripwright", version=version("tripwright"), instructions=server_instructions(), lifespan=lifespan)

    def identity() -> str:
        owner = request_user.get() or local_user
        if not owner:
            raise ValueError("Authenticated identity required")
        return owner

    # As each tool behaves (s4b): ChatGPT counts a tool without readOnlyHint as a write, and Pro offers only reads.
    # Only state and explain change nothing. query records each lookup as evidence (committed and pushed with a
    # repository), and every call adds a new record; commit replaces the current plan (earlier versions stay in the
    # history); result keeps acknowledged messages and the job's delivered state, and cancel stops a job.
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    lookup = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
    replace = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

    @server.tool(title="Receive each user message and compile relevant constraints", annotations=write, structured_output=True,
                 description=TURN_DESCRIPTION)
    @expose_errors
    def turn(text: str, request_id: str | None = None, nature: SourceNature = "host_restatement", native_ref: str | None = None,
             channel: str = "conversation", subject: list[str] | None = None, scope: dict[str, Any] | None = None,
             scenario: Literal["quick", "status", "replan", "plan", "compare", "requirements", "system"] = "plan",
             focus_keys: list[str] | None = None, exclude_keys: list[str] | None = None, speaker: str | None = None,
             record: RecordArguments | None = None, new_trip: str | None = None, points: ObjectList | None = None) -> dict[str, Any]:
        return ready().turn(identity(), text, request_id, nature=nature, native_ref=native_ref, channel=channel,
                            subject=subject, scope=scope, scenario=scenario, focus_keys=focus_keys,
                            exclude_keys=exclude_keys, speaker=speaker, record=record or None, new_trip=new_trip, points=points)

    @server.tool(title="Look up travel data with provenance", annotations=lookup, structured_output=True)
    @expose_errors
    async def query(kind: Literal["transit", "latest_departure", "web_evidence", "open_places", "nearby", "weather", "opening",
                                  "saved", "geocode"],
                    arguments: dict[str, Any]) -> dict[str, Any]:
        """Looks up travel facts; the plan is not changed. Returns sentences ready to quote, the time window searched and where the data stops. arguments is an object. transit: {origin,destination,when,offset,arrive_by?,until?}; latest_departure: {origin,destination} for tonight's latest, optional anchor_id/by/board_at for a deadline; open_places: {near,at?,km?,text?}; web_evidence: {url,quote,claim,type,read_from}; weather (only when weather is asked): {place: stored ID|stored name|{name,lat,lon},date}; origin/destination/near also accept {name,lat,lon}; nearby: {lat,lon,km?}; saved/geocode: {text}; opening: {place,date?}. Places accept IDs or stored names. Full nested shapes are validated by the service, which names any bad field. Every fact keeps its fetch time, validity and source. A fact that could not be checked comes back marked unverified."""
        return await (await opening.get_async()).query(identity(), kind, arguments, from_host=True)

    @server.tool(title="Propose plan or ledger changes against a context", annotations=write, structured_output=True)
    @expose_errors
    def propose(ctx_id: str, base_version: int | None = None, idempotency_key: str | None = None, plan: PlanArguments | None = None,
                days: ObjectList | None = None, places: ObjectList | None = None,
                bookings: ObjectList | None = None, changes: RecordArguments | None = None,
                usages: UsageArguments | None = None) -> dict[str, Any]:
        """Creates a draft from turn's ctx_id; nothing is saved until commit. plan is a complete trip (shape state(view='plan').plan). days (whole days by date, as state(view='day').json returns one), places and bookings (by id) patch the current plan; plan together with patches is refused. Without them the draft changes only the ledger. changes/usages are validated by the service; records keep their IDs and cite this turn's event in source.event. With usages or revisions omitted, the service records which applicable conditions/decisions were touched (filled_by=service) and sets no precedence between preferences; so is a condition left out. Names in speaker/subject resolve to trip person IDs. A choice the user did not state is recorded in changes.items with source.basis='agent_inference'. Only a user decision with must=true locks. Refused: a changed user decision without changes.revisions; a changed must, bought/booked item or hard node that this message's words do not carry; competing preferences before the user chose. Returns draft_id, checks, refusals, and the unverified facts the change brings in (ask_user when they wait for the user's answer, else tell_user)."""
        return ready().propose(identity(), ctx_id=ctx_id, base_version=base_version, idempotency_key=idempotency_key,
                               plan=plan, days=days, places=places, bookings=bookings, changes=changes, usages=usages)

    @server.tool(title="Commit a checked current draft", annotations=replace, structured_output=True)
    @expose_errors
    def commit(draft_id: str, idempotency_key: str | None = None, accept_unverified: bool = False) -> dict[str, Any]:
        """Saves a proposed draft as the next plan version. Refused: a stale draft, a changed user-locked field, a hard conflict, or new unverified outside facts (opening hours, timetable, weather, ticket, hard node) before a further user message has arrived through turn (the refusal lists them). accept_unverified=true states that the user agreed after seeing them. Returns the version and write state."""
        service = ready()
        return service.commit(identity(), draft_id, idempotency_key, accept_unverified=accept_unverified,
                              ask_first=service.asks_first(identity()))

    @server.tool(title="Retrieve or cancel the persistent second-response result", annotations=replace, structured_output=True)
    @expose_errors
    def result(job_id: str, cancel: bool = False, ack_events: list[str] | None = None) -> dict[str, Any]:
        """Returns a persistent job's result; cancel=true stops the job. ack_events names later messages that change nothing for it. A completed deep check returns its draft and verdict; commit does not accept a stale result."""
        return ready().result(identity(), job_id, cancel=cancel, ack_events=ack_events)

    @server.tool(title="Read committed trip, day, decisions or profile", annotations=read, structured_output=True)
    @expose_errors
    def state(view: Literal["current", "plan", "day", "blank", "ledger", "decisions", "graph", "history", "profile", "memory", "pack", "sync", "export"] = "current",
              date: str | None = None, draft_id: str | None = None) -> dict[str, Any]:
        """Reads committed state. current/plan return the trip; day needs date=YYYY-MM-DD and returns text plus json, the day object that propose(days=[json]) takes; blank returns a minimal new-trip plan. Other views: ledger, decisions, graph, history, profile, memory, pack (draft_id required), sync (Git copy; public page address, none until a public GitHub repository is connected), export. No view writes."""
        return ready().state(identity(), view, date, draft_id)

    @server.tool(title="Explain decisions, sources and preference use", annotations=read, structured_output=True)
    @expose_errors
    def explain(record_id: str | None = None, version: int | None = None) -> dict[str, Any]:
        """Explains a ledger record by record_id, or a plan version by version. Returns its source, current status, relations, usages and changelog. Read only."""
        return ready().explain(identity(), record_id, version)

    return server


def refreshing(app, service: "Tripwright", *, wait: float = 15.0):
    """Before an authenticated request is answered: note the activity, and when the server was idle (it stopped
    polling the remote), fetch once so the answer sees changes made elsewhere meanwhile. A slow remote delays the
    answer at most `wait` seconds; the fetch then finishes in the background."""
    async def middleware(scope, receive, send):
        committer = service.committer
        if scope["type"] == "http" and committer is not None and committer.activity() and committer.repo.remote():
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.to_thread(committer.sync)), wait)
            except Exception as error:  # the answer goes ahead from the index as it is
                log.warning("fetch before answering failed: %s: %s", type(error).__name__, error)
        return await app(scope, receive, send)
    return middleware


def http_app(service: "Tripwright", *, host: str = "127.0.0.1", transport_security=None):
    """The MCP transport mounted by remote.app after that one entry has checked the URL key."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    server = create_server(service, local_user=None, manage_jobs=False)
    transport = server.streamable_http_app(stateless_http=True, json_response=True, max_request_body_size=1024 * 1024,
                                           host=host, transport_security=transport_security)
    committer = service.committer
    if committer is not None:
        committer.active_window = float(os.environ.get("TRIPWRIGHT_ACTIVE_WINDOW") or 600)
    answered = refreshing(transport, service)
    routes = [Mount("/", app=answered)]

    @asynccontextmanager
    async def lifespan(app):
        async with transport.router.lifespan_context(transport):
            service.jobs.start()
            if service.committer is not None:
                service.committer.start()
            try:
                yield
            finally:
                service.jobs.stop()
                if service.committer is not None:
                    service.committer.stop()

    return Starlette(routes=routes, lifespan=lifespan)
