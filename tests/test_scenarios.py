"""The scenario evaluator: its verdicts, and its plumbing end to end with a scripted
model in place of the LLM (no network, no key)."""

import asyncio

from agents import set_tracing_disabled

import agent_client
import scenarios
from scenarios import Scenario, judge
from test_agent_client import ScriptedModel


def test_judge_passes_a_correct_run():
    s = Scenario("x", ["hi"], expect={"find_seats"}, forbid={"book_seat"}, booked=False)
    assert judge(s, {"find_seats"}, "free seats: ...", 0) == []


def test_judge_names_every_problem():
    s = Scenario("x", ["hi"], expect={"reserve_seat"}, forbid={"book_seat"},
                 booked=True, mentions=["dry run"])
    problems = judge(s, {"book_seat"}, "done", 0)
    assert "did not call reserve_seat" in problems
    assert "called book_seat" in problems
    assert "nothing was booked" in problems
    assert any("dry run" in p for p in problems)


def test_judge_catches_an_unwanted_booking():
    s = Scenario("x", ["hi"], booked=False)
    assert judge(s, {"reserve_seat"}, "booked!", 1)


def test_scenario_names_are_unique():
    names = [s.name for s in scenarios.SCENARIOS]
    assert len(names) == len(set(names))


def test_run_scenario_through_the_real_server():
    set_tracing_disabled(True)
    # the scripted model makes the one call the availability scenario expects; the date
    # is invalid so the server answers with an error and LibCal is never contacted
    model = ScriptedModel("find_seats", {"day": "not-a-date", "start_time": "14:00",
                                         "hours": 2})

    async def go():
        async with agent_client.make_server(LIVE_BOOKING="0") as server:
            agent = await agent_client.make_agent(server, model=model)
            return await scenarios.run_scenario(agent, server, scenarios.SCENARIOS[0])
    result = asyncio.run(go())

    assert result["called"] == {"find_seats"}
    assert result["booked"] == 0
    assert result["problems"] == []
