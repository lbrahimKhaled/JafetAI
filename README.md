# JafetAI: an MCP toolkit for library seat reservations

EECE 503P/798S, assignment C5 (Agent Tools).

JafetAI reserves seats in AUB's Jafet Library Reference Reading Room through
[LibCal](https://aub.edu.lb.libcal.com/reserve/spaces/rsasr). It is a FastMCP server
with exactly three tools, one per category, driven by an agent built with the OpenAI
Agents SDK.

| Tool | Category | What it does |
|---|---|---|
| `find_seats(day, start_time, hours)` | **Data** | Reads LibCal's live availability grid and returns the bookable options for a window: exact matches, or a shifted, shorter or split-across-seats alternative. Changes nothing. |
| `book_seat(seat_id, start, end, student_name, student_id, email, program)` | **Action** | Reserves one seat segment. Re-checks the slot first and returns `conflict` if it was taken. A dry run (validated, recorded, not sent) unless `LIVE_BOOKING=1`. |
| `reserve_seat(day, start_time, hours, student_name, student_id, email, program)` | **Orchestration** | Calls `find_seats`, then `book_seat` on the first exact match, and moves on to the next seat (up to 3) if that one was taken in between. If no exact match exists it books nothing and returns the alternatives so the student can choose. |

Pure lookups are MCP **resources**, not tools, so the toolkit stays at three:
`jafet://today`, `jafet://availability/{day}`, `jafet://bookings/{email}`.

## Architecture

```
 you ── agent_client.py ─────────────────────────────── MCP Host
          OpenAI Agents SDK  Agent(mcp_servers=[...])     picks the tool for each request
          └── MCPServerStdio ────────────────────────── MCP Client
                 │  JSON-RPC over stdio (launches server.py as a child process)
          server.py  FastMCP ───────────────────────── MCP Server
          ├── find_seats    ─┐
          ├── book_seat     ─┤  jafet/scraper.py  LibCal grid + seat names
          ├── reserve_seat  ─┘  jafet/booking.py  LibCal booking flow (dry run by default)
          └── resources         jafet/db.py       sqlite: seat-name cache + booking history
                                jafet/validation.py  student-detail checks
```

## Failure handling

Predictable failures come back as clear errors, never as silence:

| Failure | What the agent gets |
|---|---|
| Bad date / time format, a day outside today to +5 days | `ToolError`, e.g. `2020-01-01 is not bookable: the room takes bookings from today (…) up to 5 days ahead (…)` |
| `hours` outside 1–4, `program` not Undergraduate/Graduate | Schema validation error (bounds are in the input schema) |
| Non-AUB email, non-digit ID, empty name | `ToolError: cannot book, fix these first: …`. `reserve_seat` checks this before it contacts LibCal. |
| LibCal unreachable | `ToolError: LibCal (the library's booking system) is unreachable right now (…); nothing was booked` |
| Slot taken between search and booking | `book_seat` → `status: conflict`; `reserve_seat` tries the next seat |
| No seat free for the whole window | `reserve_seat` → `status: unavailable` + `alternatives`, nothing booked |

A `ToolError` reaches the model as an MCP `isError` result carrying the message, and
the agent is told to explain it to the student.

## Setup

Python 3.11+.

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env    # then add a model key
```

The agent's model is any OpenAI Agents SDK model name, set with `MODEL`. The default,
`litellm/deepseek/deepseek-v4-flash`, goes through LiteLLM and needs only
`DEEPSEEK_API_KEY`. The assignment requires the OpenAI Agents *SDK*, not OpenAI
models: the SDK is model-agnostic. To use OpenAI instead, set `MODEL` to an OpenAI model name and
`OPENAI_API_KEY`. With an OpenAI key, runs are also traced in the OpenAI dashboard;
without one, tracing is switched off.

`LIVE_BOOKING=1` submits real LibCal reservations. Leave it off for testing and the
demo: bookings are then validated against live availability and recorded as `dry_run`
in `jafet.db`, but never sent.

## Run

```bash
.venv/bin/python agent_client.py
```

This starts the agent. It launches `server.py` itself over stdio, so you don't run
the server separately. Each turn prints the round trip:

```
you: what's free tomorrow 2-4pm?
  [jafet-server] find_seats(day=2026-11-02, start_time=14:00, hours=2)
  -> agent calls find_seats({"day": "2026-11-02", "start_time": "14:00", "hours": 2})
  <- server returns {"day": "2026-11-02", "requested_start": "2026-11-02 13:30:00", ...}
JafetAI: Seat 014 is free from 1:30 to 3:30 pm ...
```

REPL commands that talk to the server without the model:
`/availability YYYY-MM-DD`, `/bookings email` (resource reads), and
`/call <tool> <json>` (calls a tool by hand, which is handy to show an error).
For a single turn, pass the message as arguments:
`python agent_client.py "what's free tomorrow at 2pm?"`.

To use the server from another MCP host (Claude Desktop, MCP Inspector, …), point it at
`python /path/to/server.py`. To list its tools from the command line (with the venv
activated, since the CLI launches `fastmcp` from your PATH):

```bash
fastmcp list server.py
```

## Tests

```bash
.venv/bin/pytest -v
```

52 tests. LibCal is mocked, so they need no network, no key, and book nothing.

- `tests/test_server.py`: each tool through a real MCP client (FastMCP in-memory):
  exactly three tools with descriptions and output schemas, find/book/reserve happy
  paths, every failure case in the table above, the reserve-seat retry, the resources.
- `tests/test_agent_client.py`: the OpenAI Agents SDK client launching `server.py` over
  stdio. It lists the tools, reads `jafet://today`, gets a clear error back, and runs a
  full `Runner.run` round trip with a scripted model in place of the LLM.
- `tests/test_scenarios.py`: the scenario evaluator below, its verdicts and its
  plumbing.
- `tests/test_scraper.py`: the LibCal grid parsing, slot planning and booking flow
  underneath.

## Evaluating the agent

The tests prove the tools work. `scenarios.py` checks the *agent*: given real requests,
does it pick the right tool, refuse what it should, and book only when it should? It
runs the real model against live LibCal, always as a dry run against a throwaway
database (it forces `LIVE_BOOKING=0`).

```bash
.venv/bin/python scenarios.py --when "tomorrow" -v
```

| Scenario | Request | Passes when |
|---|---|---|
| `availability_question` | "What seats are free tomorrow from 2 to 4pm?" | calls `find_seats`, books nothing |
| `one_shot_booking` | "Book me a seat … I'm Test User, ID …, email …, undergraduate." | calls `reserve_seat`, one booking recorded, reply says dry run |
| `negotiated_booking` | Asks to book, gives the details in a second message | calls `reserve_seat` after the details arrive, one booking |
| `bad_email` | Same booking with a `@gmail.com` address | nothing booked, reply mentions `mail.aub.edu` |
| `too_long` | 9am to 5pm (8 hours) | nothing booked, reply mentions the 4-hour limit |
| `too_far_ahead` | A date in 2099 | nothing booked |
| `off_topic` | "What's the capital of France?" | no tool called |

It prints a pass/fail table and exits non-zero if any scenario fails. Pick a `--when`
on which the room is open, or the booking scenarios correctly come back unavailable.

## Deliverables

- Written summary (Part 1): [`docs/SUMMARY.md`](docs/SUMMARY.md)
- Reflection: [`docs/REFLECTION.md`](docs/REFLECTION.md)
- Screencast plan: [`docs/SCREENCAST.md`](docs/SCREENCAST.md)
- FastMCP server: [`server.py`](server.py); Agents SDK host/client:
  [`agent_client.py`](agent_client.py); agent evaluation:
  [`scenarios.py`](scenarios.py)

## Notes

- Room rules the tools enforce: 1–4 hours, today up to 5 days ahead, slots on the half
  hour. QR check-in at the desk is mandatory; unscanned reservations auto-cancel.
- The live submit path (`jafet/booking.py::submit_live`) replicates the real LibCal
  flow captured from the site: cart add per slot, then submit times, then submit booking.
- There is no caller authentication: any valid AUB email can be booked for. Keep
  `LIVE_BOOKING` off unless the server is only reachable by you.
