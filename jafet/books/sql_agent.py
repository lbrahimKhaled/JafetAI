from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .. import guardrails
from ..config import get_model
from . import bookdb


def query_books_db(sql: str) -> dict:
    """Run one read-only SELECT on the books table and return its columns and rows."""
    err = guardrails.check_sql(sql)
    if err:
        return {"error": err}
    if "limit" not in sql.lower():
        sql = sql + " limit 50"
    return bookdb.run_select(sql)


INSTRUCTION = """You answer questions about AUB library books by writing SQL.

There is one table, books:
  id int, mms_id text, title text, author text, isbn text, publisher text,
  place text, edition text, year int, language text, format text,
  subjects text[], call_number text, library text, location text,
  availability_status text ('available' / 'unavailable'), description text,
  created_at timestamptz.

subjects is a text array: match it with 'x' = any(subjects) or with
array_to_string(subjects, ' ') ilike '%x%'. Student phrasing rarely matches the
catalog exactly, so match text with ilike and % wildcards, never with =.

Your loop:
1. Think about what the question actually asks.
2. Write ONE SELECT statement.
3. Call query_books_db with it.
4. Read the rows that came back.
5. If it errored or came back empty, refine and retry: broaden the ilike pattern,
   drop a filter, or search the author's last name only. At most 4 tool calls.
6. Answer concisely using ONLY the rows you got back - never invent a book, an
   author, a call number or a count. Include title, author, call_number and
   availability when they are relevant to the question.

If nothing matched after your retries, say plainly that nothing in the catalog
matched. Only SELECT statements, and never select the embedding column."""

sql_agent = LlmAgent(
    name="book_sql",
    model=get_model(),
    description="Answers structured questions about the library book database "
                "(availability, authors, years, counts, call numbers). "
                "Input should be a natural-language question.",
    instruction=INSTRUCTION,
    tools=[query_books_db],
    before_tool_callback=guardrails.validate_sql_args,
    # AgentTool runs this agent in its own runner, so the root's after_tool_callback
    # never sees query_books_db - redaction and the payload cap have to live here
    after_tool_callback=guardrails.sanitize_tool_output,
)

sql_agent_tool = AgentTool(agent=sql_agent)
