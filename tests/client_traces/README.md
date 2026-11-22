# Client traces

A trace is the tool-call order one real client used for a few user messages, replayed through the MCP server by
`tests/core/test_client_traces.py`. Each `*.json` file names the client and the day it was seen, the user messages, and
every call in order with its arguments; a value the client took from an earlier answer (`ctx_id`, `draft_id`,
`restore_version`) is written `{"$from": <step>, "path": "<dotted path>"}`. A step may say what its answer must hold
(`expect`: `path` with `equals` or `includes`), that it is refused (`refused`: words of the error), or that it could not
be sent because an earlier call was refused (`not_sent`); `after` holds read-only calls that check where the trip ended
up. The orders come from live sessions with real hosts where a host did something no unit test had tried first. To add
one, write the calls as the host made them on the fictional demo trip (`examples/fjord_demo.yaml`, never real trip
data), with the outcome the service gives today; every later change to the ledger or plan design must pass them all.
