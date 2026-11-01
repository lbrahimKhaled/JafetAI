# Screencast plan (5–7 min)

Before recording: `.env` has a model key, `LIVE_BOOKING` is off, `rm -f jafet.db` for a
clean history. Pick a day within the next 5 days when the room is open; the seats
shown come from live LibCal, so rehearse the queries once on the day you record.
Keep the server log lines (`[jafet-server] ...`) visible: they are the server half of
the round trip.

| Time | Show | Say |
|---|---|---|
| 0:00–0:45 | README architecture diagram | JafetAI books seats in Jafet's Reference Reading Room through LibCal. Three MCP tools: **find_seats** (Data), **book_seat** (Action), **reserve_seat** (Orchestration). The agent is the OpenAI Agents SDK; the server is FastMCP. |
| 0:45–2:00 | `server.py`, then `agent_client.py` | Typed arguments and Pydantic outputs. `reserve_seat` calls `find_seats` then `book_seat`. Resources for pure lookups. `MCPServerStdio` launches the server and gives its tools to the agent. |
| 2:00–2:45 | `python agent_client.py`, ask: *"What's free tomorrow from 2 to 4pm?"* | The agent picks the Data tool: `-> agent calls find_seats`, the server log line, `<- server returns`. Nothing is booked. |
| 2:45–3:45 | *"Book it. I'm Test User, ID 202400000, tu00@mail.aub.edu, undergrad."* | Orchestration: one `reserve_seat` call; the server log shows it calling find then book. The result says DRY RUN. |
| 3:45–4:30 | `/bookings tu00@mail.aub.edu` | That is a resource read, no model involved: the booking is recorded. |
| 4:30–5:15 | Failure: *"Book tomorrow 2pm for 2 hours, same details but my email is tu00@gmail.com"*. If the agent catches the bad email itself and never calls a tool, show the server side by hand: `/call book_seat {"seat_id": 1, "start": "2020-01-01 10:00:00", "end": "2020-01-01 11:00:00", "student_name": "Test User", "student_id": "202400000", "email": "tu00@gmail.com", "program": "Undergraduate"}` | The server raises a clear `ToolError` (`[ERROR] cannot book, fix these first: email must be an AUB address...`); the agent explains it and asks for a fix instead of failing silently. |
| 5:15–5:45 | Optional: a busy time, then accept a split plan | `reserve_seat` returns `unavailable` + alternatives, the student picks one, the agent calls the Action tool once per segment. |
| 5:45–6:15 | `pytest -v`, then the table from a `python scenarios.py` run you did earlier | 52 tests, LibCal mocked: each tool, every failure case, the stdio connection, a scripted-model round trip. The scenario table shows the real agent routing 7 real-world requests: right tool, refuses bad input, books only when it should. |
| 6:15–7:00 | Talking head / README | Challenges: the old single `chat` tool and splitting it; deciding that `reserve_seat` must not accept alternatives on its own; the race between search and booking (retry next seat); stdout is the JSON-RPC channel so logs go to stderr; the stdio child does not inherit the shell environment by default. Repo link. |
