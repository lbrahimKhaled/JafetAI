"""Evaluate the agent on real-world requests: does it route each one to the right tool,
refuse what it should, and book only when it should?

    python scenarios.py                    # all scenarios, "tomorrow"
    python scenarios.py --when "on Tuesday" -v
    python scenarios.py --only bad_email

Runs the real agent (agent_client.py's Agent and MCP server) against live LibCal, but
always as a dry run against a throwaway database: LIVE_BOOKING is forced off, so nothing
is ever sent to the library. Pick a --when on which the room is open, or the booking
scenarios will (correctly) come back unavailable and fail.

Each scenario checks:
  expect   tools the agent must call at least once
  forbid   tools it must not call
  booked   whether a booking must (True) or must not (False) end up recorded
  mentions text the final reply must contain (case-insensitive), e.g. the rule it broke
"""

import argparse
import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass, field

from agents import Runner, SQLiteSession, ToolCallItem

import agent_client

STUDENT = "Test User, student ID 202400000, tu00@mail.aub.edu, undergraduate"
EMAIL = "tu00@mail.aub.edu"


@dataclass
class Scenario:
    name: str
    turns: list[str]
    expect: set[str] = field(default_factory=set)
    forbid: set[str] = field(default_factory=set)
    booked: bool | None = None
    mentions: list[str] = field(default_factory=list)


SCENARIOS = [
    Scenario("availability_question",
             ["What seats are free {when} from 2 to 4pm?"],
             expect={"find_seats"}, forbid={"book_seat", "reserve_seat"}, booked=False),
    Scenario("one_shot_booking",
             ["Book me a seat {when} from 2 to 4pm. I'm " + STUDENT + "."],
             expect={"reserve_seat"}, booked=True, mentions=["dry run"]),
    Scenario("negotiated_booking",
             ["Can you book me a seat {when} from 2 to 4pm?",
              "Sure: " + STUDENT + "."],
             expect={"reserve_seat"}, booked=True),
    Scenario("bad_email",
             ["Book me a seat {when} from 2 to 4pm. I'm Test User, student ID "
              "202400000, tu00@gmail.com, undergraduate."],
             booked=False, mentions=["mail.aub.edu"]),
    Scenario("too_long",
             ["Book me a seat {when} from 9am to 5pm. I'm " + STUDENT + "."],
             booked=False, mentions=["4"]),
    Scenario("too_far_ahead",
             ["Book me a seat on 2099-01-01 at 10am for one hour. I'm " + STUDENT + "."],
             booked=False),
    Scenario("off_topic",
             ["What's the capital of France?"],
             forbid={"find_seats", "book_seat", "reserve_seat"}, booked=False),
]


def judge(s, called, reply, new_bookings):
    """Everything the scenario got wrong, as a list of reasons. Empty means pass."""
    problems = []
    for t in sorted(s.expect - called):
        problems.append(f"did not call {t}")
    for t in sorted(s.forbid & called):
        problems.append(f"called {t}")
    if s.booked is True and new_bookings == 0:
        problems.append("nothing was booked")
    if s.booked is False and new_bookings > 0:
        problems.append(f"booked {new_bookings} segment(s) it should not have")
    for m in s.mentions:
        if m.lower() not in (reply or "").lower():
            problems.append(f"reply does not mention {m!r}")
    return problems


async def count_bookings(server):
    return len((await agent_client.read_json(server, "jafet://bookings/" + EMAIL))["bookings"])


async def run_scenario(agent, server, s, when="tomorrow", verbose=False):
    session = SQLiteSession("scenario-" + s.name)  # fresh conversation per scenario
    before = await count_bookings(server)
    called, reply = set(), ""
    for text in s.turns:
        message = text.format(when=when)
        if verbose:
            print(f"  you: {message}")
        result = await Runner.run(agent, message, session=session, max_turns=12)
        called |= {i.tool_name for i in result.new_items if isinstance(i, ToolCallItem)}
        reply = str(result.final_output)
        if verbose:
            agent_client.show_round_trip(result)
            print(f"  JafetAI: {reply}\n")
    new = await count_bookings(server) - before
    return {"name": s.name, "called": called, "booked": new,
            "problems": judge(s, called, reply, new)}


async def main(args):
    chosen = [s for s in SCENARIOS if not args.only or s.name in args.only]
    with tempfile.TemporaryDirectory() as tmp:
        # dry run, always, and a database nobody else reads
        server = agent_client.make_server(LIVE_BOOKING="0",
                                          JAFET_DB=os.path.join(tmp, "eval.db"))
        async with server:
            agent = await agent_client.make_agent(server, model=args.model)
            results = []
            for s in chosen:
                print(f"--- {s.name}")
                try:
                    results.append(await run_scenario(agent, server, s, args.when,
                                                      args.verbose))
                except Exception as e:  # a model/API failure fails this one scenario
                    results.append({"name": s.name, "called": set(), "booked": 0,
                                    "problems": [f"run failed: {type(e).__name__}: {e}"]})

    print(f"\n{'scenario':24} {'result':6} {'tools called':34} booked")
    for r in results:
        verdict = "PASS" if not r["problems"] else "FAIL"
        tools = ", ".join(sorted(r["called"])) or "-"
        print(f"{r['name']:24} {verdict:6} {tools:34} {r['booked']}")
        for p in r["problems"]:
            print(f"{'':31}- {p}")
    passed = sum(not r["problems"] for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--when", default="tomorrow",
                    help='day phrase used in the requests, e.g. "on Tuesday"')
    ap.add_argument("--only", nargs="*", help="scenario names to run")
    ap.add_argument("--model", help="override MODEL for this run")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every round trip")
    sys.exit(asyncio.run(main(ap.parse_args())))
