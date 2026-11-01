"""JafetAI agent: the MCP Host/Client, built with the OpenAI Agents SDK.

    python agent_client.py                      # interactive chat
    python agent_client.py "what's free tomorrow 2-4pm?"   # one turn, then exit

The SDK's MCPServerStdio is the MCP client: it launches server.py as a child process,
lists its three tools and hands them to the Agent, which decides which one each user
request needs. Every tool call is printed (agent -> client -> server) together with the
server's own log line and the result coming back, so the full round trip is visible.

REPL commands that talk to the MCP server directly (no model involved):
    /availability YYYY-MM-DD           read the availability resource
    /bookings someone@mail.aub.edu     read the booking-history resource
    /call <tool> <json args>           call a tool by hand, e.g. to show its error
    /quit
"""

import asyncio
import json
import os
import sys

from dotenv import load_dotenv

from agents import (Agent, Runner, SQLiteSession, ToolCallItem, ToolCallOutputItem,
                    set_tracing_disabled)
from agents.mcp import MCPServerStdio

load_dotenv()

import litellm  # noqa: E402  quiet its "Give Feedback" banner on every API error

litellm.suppress_debug_info = True

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")

# Any Agents SDK model name. "litellm/<provider>/<model>" goes through LiteLLM, so a
# DeepSeek key is enough; set MODEL to a plain OpenAI model name to use OpenAI instead.
MODEL = os.environ.get("MODEL", "litellm/deepseek/deepseek-v4-flash")

INSTRUCTIONS = """You are JafetAI, the seat reservation assistant for AUB's Jafet Library
Reference Reading Room (single silent seats). Today is {weekday} {today}; the last bookable
day is {last_bookable_day}. Resolve relative dates ("tomorrow", "Friday") against today.

Room rules: 1 to 4 hours per booking, today up to 5 days ahead, slots start on the half hour.

You have three tools. Pick the one the request needs:
- find_seats (Data, read-only): the student asks what is free, or you need options to
  negotiate. Never offer a seat or time that did not come from find_seats.
- reserve_seat (Orchestration): the student wants a specific window and you have all of
  their details. It searches and books the exact match in one call. If it comes back
  "unavailable", present the alternatives it returned and let the student choose.
- book_seat (Action): the student agreed to a specific alternative (for example a split
  plan across seats): call it once per segment, with the exact seat_id/start/end returned.

To book you need: full name, student ID (digits only), AUB email ending in @mail.aub.edu,
and Undergraduate or Graduate. If something is missing or invalid, ask for just that piece,
one short question at a time. Never invent or assume these values.

If a tool returns an error, explain it to the student in plain words and say what to do
next. Always say whether a booking was a DRY RUN (validated, not sent to LibCal) or real.
Stay on topic: Reading Room seats only."""


def short(value, limit=400):
    s = value if isinstance(value, str) else json.dumps(value)
    return s if len(s) <= limit else s[:limit] + " ..."


def show_round_trip(result):
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            args = getattr(item.raw_item, "arguments", None) or ""
            print(f"  -> agent calls {item.tool_name}({short(args, 300)})")
        elif isinstance(item, ToolCallOutputItem):
            print(f"  <- server returns {short(item.output)}")


async def read_json(server, uri):
    res = await server.read_resource(uri)
    return json.loads(res.contents[0].text)


async def direct_command(server, line):
    cmd, _, arg = line.partition(" ")
    if cmd == "/call":
        name, _, args = arg.strip().partition(" ")
        try:
            res = await server.call_tool(name, json.loads(args or "{}"))
        except Exception as e:  # bad JSON, unknown tool
            print(f"  call failed: {e}")
            return
        label = "ERROR" if res.is_error else "ok"
        print(f"  [{label}]", " ".join(getattr(c, "text", "") for c in res.content))
        return
    uri = {"/availability": "jafet://availability/", "/bookings": "jafet://bookings/"}.get(cmd)
    if uri is None or not arg.strip():
        print("  usage: /availability YYYY-MM-DD | /bookings email | "
              "/call tool {json} | /quit")
        return
    try:
        print(json.dumps(await read_json(server, uri + arg.strip()), indent=2))
    except Exception as e:  # a resource error (bad day, LibCal down) is just shown
        print(f"  resource error: {e}")


def make_server(**env):
    """The MCP client: launches server.py as a stdio child process."""
    return MCPServerStdio(
        name="jafet",
        # the MCP SDK hands a stdio child only a minimal environment by default, which
        # would silently drop LIVE_BOOKING / JAFET_DB set in this shell
        params={"command": sys.executable, "args": [SERVER],
                "env": {**os.environ, **env}},
        cache_tools_list=True,
        # one reserve_seat is up to three LibCal round trips at 20 s each
        client_session_timeout_seconds=90,
    )


async def make_agent(server, model=None):
    """The agent, with today's date read from the server's jafet://today resource."""
    if not os.environ.get("OPENAI_API_KEY"):
        set_tracing_disabled(True)  # traces upload to OpenAI; nothing to upload to
    today = await read_json(server, "jafet://today")
    return Agent(name="JafetAI", instructions=INSTRUCTIONS.format(**today),
                 mcp_servers=[server], model=model or MODEL)


async def main(one_shot):
    async with make_server() as server:
        tools = await server.list_tools()
        print("connected to MCP server 'jafet', tools:", ", ".join(t.name for t in tools))
        agent = await make_agent(server)
        session = SQLiteSession("jafet-cli")  # in memory: carries the negotiation

        async def turn(message):
            result = await Runner.run(agent, message, session=session, max_turns=12)
            show_round_trip(result)
            print("JafetAI:", result.final_output, "\n")

        if one_shot:
            await turn(one_shot)
            return
        print(f"model {MODEL}. Ask about seats, or /availability, /bookings, /call, /quit\n")
        while True:
            try:
                line = input("you: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line.startswith("/"):
                await direct_command(server, line)
                continue
            await turn(line)


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:])))
