import datetime
import decimal
import json

from jafet import guardrails
from jafet.books import bookdb, sql_agent


def test_check_sql_accepts_selects():
    assert guardrails.check_sql("select title, author from books where year > 1990") is None
    assert guardrails.check_sql(
        "  with recent as (select * from books where year > 2000) "
        "select title from recent") is None


def test_check_sql_rejects_writes():
    for sql in ["insert into books(title) values('x')",
                "update books set year = 1",
                "delete from books",
                "drop table books",
                "select 1; drop table books"]:
        assert guardrails.check_sql(sql) is not None, sql


def test_check_sql_rejects_comment_smuggling():
    assert guardrails.check_sql("select 1 -- x") is not None
    assert guardrails.check_sql("select /* x */ 1 from books") is not None


def test_check_sql_rejects_catalog_and_embedding():
    assert guardrails.check_sql("select * from pg_catalog.pg_tables") is not None
    assert guardrails.check_sql("select embedding from books") is not None


def test_check_sql_rejects_non_select_start():
    assert guardrails.check_sql("explain select * from books") is not None
    assert guardrails.check_sql("") is not None


class FakeTool:
    def __init__(self, name):
        self.name = name


def test_validate_sql_args():
    assert guardrails.validate_sql_args(FakeTool("book_seat"), {"sql": "drop table books"},
                                        None) is None
    assert guardrails.validate_sql_args(FakeTool("query_books_db"),
                                        {"sql": "select title from books"}, None) is None
    out = guardrails.validate_sql_args(FakeTool("query_books_db"),
                                       {"sql": "delete from books"}, None)
    assert out["status"] == "rejected" and len(out["problems"]) == 1


def test_query_books_db_appends_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(bookdb, "run_select", lambda sql: seen.append(sql) or {"rows": []})
    sql_agent.query_books_db("select title from books")
    sql_agent.query_books_db("select title from books limit 5")
    assert seen == ["select title from books limit 50",
                    "select title from books limit 5"]


def test_query_books_db_rejects_before_running(monkeypatch):
    def boom(sql):
        raise AssertionError("run_select must not be called")
    monkeypatch.setattr(bookdb, "run_select", boom)
    out = sql_agent.query_books_db("drop table books")
    assert "error" in out


def test_sql_agent_sanitizes_its_own_tool_output():
    # the root agent's callback never sees query_books_db, AgentTool runs this one
    assert sql_agent.sql_agent.after_tool_callback is guardrails.sanitize_tool_output


class FakeCursor:
    def __init__(self, cols, rows):
        self.description = [type("Col", (), {"name": c}) for c in cols]
        self.rows = rows

    def execute(self, sql):
        pass

    def fetchmany(self, n):
        return self.rows[:n]


def fake_conn(monkeypatch, cols, rows):
    cur = FakeCursor(cols, rows)
    conn = type("Conn", (), {"execute": lambda self, sql: cur,
                             "rollback": lambda self: None,
                             "close": lambda self: None})()
    monkeypatch.setattr(bookdb, "conn", lambda: conn)


def test_run_select_refuses_embedding_from_star(monkeypatch):
    fake_conn(monkeypatch, ["title", "embedding"], [["Deep Learning", "[0.1,0.2]"]])
    out = bookdb.run_select("select * from books")
    assert "embedding" in out["error"] and "rows" not in out


def test_run_select_rows_are_jsonable(monkeypatch):
    stamp = datetime.datetime(2026, 8, 20, 17, 48, tzinfo=datetime.timezone.utc)
    fake_conn(monkeypatch, ["title", "subjects", "created_at", "avg"],
              [["Deep Learning", ["Artificial intelligence"], stamp, decimal.Decimal("1995.5")]])
    out = bookdb.run_select("select title, subjects, created_at, avg(year) from books")
    json.dumps(out)  # would raise before dates and decimals were coerced
    assert out["rows"][0][1] == ["Artificial intelligence"]
    assert out["rows"][0][3] == "1995.5"
