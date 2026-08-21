# JafetAI

JafetAI is an intelligent library companion for the American University of Beirut. It reserves seats in the Jafet Library Reference Reading Room through [LibCal](https://aub.edu.lb.libcal.com/reserve/spaces/rsasr) (with back-and-forth negotiation over missing info and unavailable slots) and finds books in the AUB collection — semantic recommendations over a pgvector database scraped from the [library catalog](https://libcat.aub.edu.lb), plus a SQL sub-agent for availability and metadata questions.

## Architecture

```
user / other chatbot ── HTTP (http_server.py or adk api_server) or MCP (mcp_server.py)
        │                       both go through jafet/service.py
        │
   root orchestrator (Google ADK, LiteLLM)
   │      routes seats vs books, rewrites RAG queries, reviews tool results
   ├── seat tools      live LibCal availability grid + seat names (scraper)
   ├── sqlite          cache of seats + booking history (my_bookings tool)
   ├── book_seat       LibCal booking flow (dry-run unless LIVE_BOOKING=1)
   ├── search_books    RAG: query embedding -> cosine top-5 over pgvector
   └── book_sql        ReAct sub-agent: SELECT-only SQL over the books table

offline: jafet/books/ingest.py ── Primo REST API -> descriptions -> embeddings -> Postgres
         (150 held books across 10 broad subjects, text-embedding-3-large, 3072d)
         its output is checked in as seed/books.sql.gz, which is what a fresh
         `docker compose up` loads, so ingest is a refresh and not a setup step
```

Two containers: `jafet` (the chat server) and `books-db` (pgvector, seeded on first start).
ClassMate's chatbot is a third, in its own stack, reaching `jafet` over a shared network —
see [ClassMate](#classmate).

Guardrails: prompt-injection blocklist before the model, deterministic booking-arg and SELECT-only SQL validation before tools, secret redaction + payload caps after the book tools. When the caller signed the student in, `book_seat` is also pinned to that email from session state, so one valid AUB address cannot stand in for another.

## Run

Both containers, and the database comes up already holding the books:

```bash
docker network create classmate-net   # once, ever -- shared with the ClassMate stack
docker compose up -d --build
curl http://127.0.0.1:8802/health
```

That is the whole setup. `seed/books.sql.gz` is a dump of the 150 catalog books and their
embeddings, mounted into Postgres' `docker-entrypoint-initdb.d`, so the first time the
volume is created the table is loaded from it — no scrape, no embedding spend, no ingest
step to remember. It only runs on an *empty* volume: `docker compose down -v` is what
re-seeds, and a plain `down`/`up` keeps whatever is in there.

You need a `.env` next to the compose file first; the keys are below.

To refresh the catalog for real (scrape → describe → embed → load, costs NVIDIA and OpenAI
calls) and then re-cut the seed:

```bash
docker compose run --rm ingest
docker exec jafet-books-db pg_dump -U jafet -d jafet_books --data-only --table=books \
  | grep -v '^\\restrict' | grep -v '^\\unrestrict' | gzip -9 > seed/books.sql.gz
```

### Other entrypoints

The container runs `http_server.py`. The rest are host-side, against the same DB
(`.venv/bin/pip install -r requirements.txt` first):

```bash
adk api_server --port 8801 .   # full ADK REST on http://localhost:8801
adk web                        # dev UI in the browser
python mcp_server.py           # MCP stdio server -- stdio, so it is not a compose service
```

`.env` keys used:

- `DEEPSEEK_API_KEY` — production default model (`deepseek/deepseek-v4-flash`)
- `MODEL` — optional LiteLLM override, e.g. `openrouter/<model>` or `nvidia_nim/<model>`
- `OPEN_AI_KEY` — OpenAI embeddings (`text-embedding-3-large`), used by ingest and per search query
- `NVIDIA_API_KEY` — description generation during ingest (NVIDIA NIM)
- `JAFET_PG_DSN` — Postgres override. Compose sets it to `books-db:5432`; host-side runs get the default `postgresql://jafet:jafet@127.0.0.1:5433/jafet_books`, which is the same database through the published port
- `LIVE_BOOKING=1` — actually submit reservations to LibCal (off by default: bookings are validated, stored as `dry_run`, never sent)

## HTTP API (for another chatbot)

`http_server.py` is one POST per turn against a session the caller names — no separate
create-session call, and the URL shape does not move with the ADK version.

```bash
curl -X POST http://127.0.0.1:8802/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "t1", "message": "book me a seat tomorrow 2-4pm",
       "student_email": "someone@mail.aub.edu"}'
# -> {"reply": "..."}
```

`session_id` is what continues a conversation: send the same one and Jafet picks the
negotiation up where it left off. Sessions are in memory, so a restart drops them and the
student repeats themselves rather than answering into a session that is gone.
`DELETE /session/{session_id}` drops one on purpose — a negotiation holds the student's
name, student ID and email, so a caller that lets someone delete the conversation should
call this too. ClassMate does, from its own delete-thread route.

`student_email` is optional. A caller that has already authenticated the student passes it
so Jafet does not ask for an email it can be told. It goes two places: into the opening
message, which only *tells* the model, and into **session state**, which
`validate_booking_args` checks — so `book_seat` cannot fire for anyone but the signed-in
student. Only the session-state half is trusted, because a student can type the message half
themselves. An address that is not `@mail.aub.edu` is dropped rather than passed on: stating
one would tell the model to stop asking for an email that the booking guardrail then rejects.

`JAFET_HTTP_HOST` / `JAFET_HTTP_PORT` move it. There is no auth on it and it reaches the
booking tools, so where it is reachable from is the decision to get right before
`LIVE_BOOKING=1`. In the container it binds `0.0.0.0`, which is not the same statement:
what can actually reach it is the compose network plus the published port, and the compose
file publishes to `127.0.0.1:8802` only. Container callers get to it by name over a private
network instead — which is the point of the setup below, and why nothing here has to be
bound wider.

### ClassMate

ClassMate's chatbot calls this endpoint from its `ask_library` tool, keyed on the ClassMate
thread id, so a student books a seat without leaving that chat. Its side is
`chatbot/classmate_rag/agents/library.py`, reading `JAFET_URL`.

The two stacks are separate compose projects joined by one external network, so either can
be brought up or torn down without the other:

```bash
docker network create classmate-net            # once, ever

cd ~/Documents/JafetAI  && docker compose up -d --build   # jafet + books-db
cd ~/Documents/Classmate && docker compose up -d --build  # the ClassMate stack
```

ClassMate's `chatbot` service joins `classmate-net` and reaches this one at
`http://jafet:8802` — a container name on a private network, so neither side publishes the
booking endpoint to a host interface. Order does not matter, because the network is created
by hand rather than owned by either compose file; if you skip that command, both stacks
fail with *network classmate-net declared as external, but could not be found*.

A Jafet that is down is not an outage for ClassMate: `library.py` catches the connection
error and tells the student to ask again, deliberately vague about whether the booking went
through, since a read timeout aborts only ClassMate's side while Jafet finishes the turn.

The ADK `api_server` still works if you want its full REST surface:

```bash
curl -X POST http://localhost:8801/apps/jafet/users/u1/sessions/s1 \
  -H "Content-Type: application/json" -d '{}'
curl -X POST http://localhost:8801/run \
  -H "Content-Type: application/json" \
  -d '{"appName": "jafet", "userId": "u1", "sessionId": "s1",
       "newMessage": {"role": "user", "parts": [{"text": "book me a seat tomorrow 2-4pm"}]}}'
```

## MCP

`mcp_server.py` exposes one tool, `chat(session_id, message)`, over stdio — point any MCP
client at it. Same `jafet/service.py` behind it as the HTTP server, so the two agree on what
a turn is. It is not a compose service, because stdio is the transport: an MCP client
launches it as a child process. Run it on the host against the published Postgres, or, to
use the image, `docker compose run --rm -T jafet python mcp_server.py`.

## Tests & evals

Host-side, against a venv — they are fully mocked, so they need neither the containers nor
a key:

```bash
.venv/bin/pytest tests/          # scraper, booking, book-scraper, RAG, guardrail + HTTP tests (mocked)
evals/eval_chatbot.ipynb          # seat conversation evals against the running API
evals/eval_books.ipynb            # book RAG/SQL/routing evals (read-only, safe on any server)
```

The notebooks do hit a running server, on `127.0.0.1:8802` and `127.0.0.1:5433` — both
published by the compose file, so `docker compose up -d` is enough to run them.

## Notes

- Booking data: student name, ID (digits), AUB email ending `@mail.aub.edu` — the bot collects and validates these in conversation, and guardrail callbacks re-check them before any booking tool call.
- The live submit path (`jafet/booking.py::submit_live`) replicates the real flow captured
  from the site (2026-08-20): cart add -> submit times (session id) -> submit booking with
  fname/lname/email + the AUB ID question (field parsed from the form at runtime).
- Library rules: QR check-in at the desk is mandatory - unscanned reservations auto-cancel,
  late arrivals are held 15 minutes.
- WARNING: with `LIVE_BOOKING=1` every confirmed chat booking is a real reservation.
  Never run `evals/eval_chatbot.ipynb` against a live server - it books with test data.
