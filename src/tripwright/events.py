from __future__ import annotations

from typing import Literal

from tripwright.storage import Store, encode, identifier, now


SourceNature = Literal["user_verbatim", "host_restatement", "screenshot_extraction", "agent_inference"]
NATURES = {"user_verbatim", "host_restatement", "screenshot_extraction", "agent_inference"}


class Events:
    def __init__(self, store: Store):
        self.store = store

    def receive(self, owner: str, text: str, request_id: str | None = None, *, nature: SourceNature = "host_restatement",
                channel: str = "conversation", native_ref: str | None = None, speaker: str | None = None,
                suspicious_words: list[dict] | None = None) -> dict:
        if nature not in NATURES:
            raise ValueError("Unknown source nature")
        request_id = request_id or identifier("REQ")
        if not text.strip() or not request_id.strip():
            raise ValueError("Message and request_id must be nonempty")
        downgraded = nature == "user_verbatim" and not native_ref
        if downgraded:
            nature = "host_restatement"
        incoming = dict(text=text, nature=nature, channel=channel, native_ref=native_ref,
                        speaker=speaker, suspicious_words=suspicious_words or [])
        with self.store.transaction():
            for event in self.store.all("events", owner):
                if event.get("request_id") == request_id:
                    if encode(event["received"]) == encode(incoming):
                        return event["receipt"]
                    # A host-generated key is not proof that two different messages are one.
                    request_id = identifier("REQ")
                    break
            event_id = identifier("E")
            receipt = dict(event_id=event_id, request_id=request_id, received_text=text, nature=nature,
                           verbatim=nature == "user_verbatim", received_at=now(), native_ref=native_ref)
            if downgraded:
                receipt["note"] = "No native message reference was supplied; recorded as host_restatement."
            self.store.put("events", owner, dict(id=event_id, request_id=request_id, received=incoming, receipt=receipt), insert=True)
            return receipt

    def append_change(self, owner: str, source_event: str, action: str, details: dict) -> str:
        self.store.get("events", owner, source_event)
        event_id = identifier("E")
        self.store.put("events", owner, dict(id=event_id, source_event=source_event,
                       action=action, details=details, created_at=now()), insert=True)
        return event_id
