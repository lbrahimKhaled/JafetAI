# Reflection

JafetAI started as a full chatbot. Its first MCP server exposed one tool,
`chat(session_id, message)`, which handed the whole agent to whoever called it. That is
exactly the "one tool that does everything" anti-pattern, so the line-drawing started
with taking it apart. I split it by *effect*. `find_seats` only reads; `book_seat`
changes something and only one thing (a single seat segment); `reserve_seat` composes
the two and owns the one piece of logic neither should have: which exact match to try,
and moving on to the next seat when the first was taken between the search and the
booking. The hardest boundary was what `reserve_seat` may decide on its own. It books
only an exact match. If the requested window is not free it returns the alternatives
and books nothing, because accepting a shorter stay or a split across seats is the
student's decision, not the tool's, and that negotiation belongs in the agent loop.
The student-detail validation runs in both booking tools on purpose: the action tool
must be safe on its own, and the orchestration tool rejects bad details before spending
a LibCal round trip on a search it will not use.

Three tools fit the booking flow, but only because the pure lookups (today's date,
per-slot availability, booking history) became MCP resources instead of tools. That
has a real cost: the OpenAI Agents SDK gives the model tools, not resources, so the
agent cannot answer "what did I book?". The host only fills today's date into the
instructions, and history is reachable through a REPL command. In a real deployment
I would add `cancel_booking` (a second action, which the room's 4-hours-a-day rule
practically requires) and expose history as a data tool. Next steps beyond that:
bind the booking email to an authenticated caller (MCP's OAuth support) so one student
cannot book as another, serve over Streamable HTTP so ClassMate and other hosts can
share one server, and build a small evaluation set of conversations that checks the
agent picks the right tool for each request.
