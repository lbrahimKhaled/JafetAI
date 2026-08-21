from jafet import guardrails


class FakeTool:
    def __init__(self, name):
        self.name = name


def sanitize(name, response):
    return guardrails.sanitize_tool_output(FakeTool(name), {}, None, response)


SMALL = {"query": "artificial intelligence", "matches": [
    {"title": "Deep Learning", "author": "Goodfellow, Ian",
     "call_number": "Q325.5 .G66 2016", "availability_status": "Available"}]}


def test_other_tools_pass_through():
    assert sanitize("book_seat", {"status": "ok"}) is None
    assert sanitize("find_seats", {"secret": "sk-abcdefgh12345678"}) is None


def test_small_clean_payload_untouched():
    assert sanitize("search_books", SMALL) is None
    assert sanitize("query_books_db", {"columns": ["title"], "rows": [["Deep Learning"]]}) is None


def test_redacts_secrets_in_nested_match():
    resp = {"query": "ai", "matches": [
        {"title": "Deep Learning",
         "description": "leaked sk-abc123XYZ_def456 in the blurb",
         "notes": ["set OPEN_AI_KEY before running"]}]}
    out = sanitize("search_books", resp)
    m = out["matches"][0]
    assert "sk-abc123XYZ_def456" not in m["description"]
    assert m["description"] == "leaked [redacted] in the blurb"
    assert m["notes"] == ["set [redacted] before running"]
    assert resp["matches"][0]["description"].startswith("leaked sk-")  # original untouched


def test_book_sql_text_answer_redacted():
    # AgentTool returns the sub-agent's answer as a plain string, not a dict
    assert sanitize("book_sql", "nothing matched") is None
    assert sanitize("book_sql", "the key is sk-abc123XYZ_def") == "the key is [redacted]"


def test_search_books_matches_truncated_to_three():
    big = [dict(SMALL["matches"][0], description="x" * 2000) for _ in range(5)]
    out = sanitize("search_books", {"query": "ai", "matches": big})
    assert len(out["matches"]) == 3


def test_query_books_db_rows_truncated_with_marker():
    rows = [["Deep Learning", "y" * 500] for _ in range(40)]
    out = sanitize("query_books_db", {"columns": ["title", "description"], "rows": rows})
    assert out["truncated"] is True
    assert 0 < len(out["rows"]) < 40
    assert len(str(out)) <= guardrails.MAX_TOOL_PAYLOAD + 100  # only the marker is extra


def test_root_agent_tool_set():
    from jafet import agent
    names = [getattr(t, "__name__", None) or t.name for t in agent.root_agent.tools]
    assert names == ["get_availability", "find_seats", "book_seat", "my_bookings",
                     "today", "read_library_page", "search_books", "book_sql"]
    assert agent.root_agent.after_tool_callback is guardrails.sanitize_tool_output
