"""The host side: the OpenAI Agents SDK's MCP client launching server.py over stdio,
the same way agent_client.py does. No model and no network: only calls whose answer
does not depend on LibCal."""

import asyncio
import json
import sys

from agents import Agent, Runner, Usage, set_tracing_disabled
from agents.items import ModelResponse
from agents.mcp import MCPServerStdio
from agents.models.interface import Model
from openai.types.responses import (ResponseFunctionToolCall, ResponseOutputMessage,
                                    ResponseOutputText)

import agent_client


def with_server(fn):
    async def go():
        server = MCPServerStdio(name="jafet",
                                params={"command": sys.executable,
                                        "args": [agent_client.SERVER]},
                                client_session_timeout_seconds=30)
        async with server:
            return await fn(server)
    return asyncio.run(go())


def test_sdk_client_sees_the_three_tools():
    async def names(server):
        return {t.name for t in await server.list_tools()}
    assert with_server(names) == {"find_seats", "book_seat", "reserve_seat"}


def test_sdk_client_reads_today_resource():
    today = with_server(lambda s: agent_client.read_json(s, "jafet://today"))
    assert {"today", "weekday", "last_bookable_day"} <= set(today)
    # the agent's instructions are filled from it
    assert today["today"] in agent_client.INSTRUCTIONS.format(**today)


def test_failure_reaches_the_sdk_as_a_clear_error():
    async def bad_call(server):
        return await server.call_tool("book_seat", {
            "seat_id": 1, "start": "2020-01-01 10:00:00", "end": "2020-01-01 11:00:00",
            "student_name": "Test User", "student_id": "202400000",
            "email": "someone@gmail.com", "program": "Undergraduate"})
    result = with_server(bad_call)
    assert result.is_error
    assert "@mail.aub.edu" in json.dumps(result.model_dump(mode="json"))


# ---- the whole loop: Agent -> MCP client -> server -> back to the Agent ----

class ScriptedModel(Model):
    """Stands in for the LLM: first asks for one tool call, then answers with whatever
    the tool returned. Records the tools it was offered and the tool output it saw."""

    def __init__(self, tool, args):
        self.tool, self.args = tool, args
        self.offered, self.saw = [], None

    async def get_response(self, system_instructions, input, model_settings, tools,
                           *args, **kwargs):
        self.offered = sorted(t.name for t in tools)
        outputs = [i for i in input if isinstance(i, dict)
                   and i.get("type") == "function_call_output"]
        if not outputs:
            call = ResponseFunctionToolCall(type="function_call", id="fc_1",
                                            call_id="call_1", name=self.tool,
                                            arguments=json.dumps(self.args))
            return ModelResponse(output=[call], usage=Usage(), response_id=None)
        self.saw = outputs[-1]["output"]
        text = ResponseOutputText(type="output_text", text="done", annotations=[])
        msg = ResponseOutputMessage(type="message", id="m_1", role="assistant",
                                    status="completed", content=[text])
        return ModelResponse(output=[msg], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def test_agent_round_trip_through_mcp():
    set_tracing_disabled(True)
    model = ScriptedModel("find_seats", {"day": "not-a-date", "start_time": "14:00",
                                         "hours": 2})

    async def run(server):
        agent = Agent(name="JafetAI", instructions="test", mcp_servers=[server],
                      model=model)
        return await Runner.run(agent, "is anything free?")
    result = with_server(run)

    assert model.offered == ["book_seat", "find_seats", "reserve_seat"]
    # the server's clear error made it all the way back to the model
    assert "YYYY-MM-DD" in str(model.saw)
    assert result.final_output == "done"
