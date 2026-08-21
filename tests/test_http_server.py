import pytest
from fastapi.testclient import TestClient

import http_server
from jafet import service

# The agent itself is stubbed: what is under test is the endpoint ClassMate calls and the
# session bookkeeping behind it, neither of which should need a model to exercise.


@pytest.fixture
def client(monkeypatch):
    seen = []

    async def run_turn(session_id, message, user_id="http", student_email=""):
        seen.append({"session_id": session_id, "message": message,
                     "user_id": user_id, "student_email": student_email})
        return "reply to " + message

    monkeypatch.setattr(http_server.service, "run_turn", run_turn)
    return TestClient(http_server.app), seen


def test_a_turn_carries_the_session_and_the_email_through(client):
    c, seen = client
    r = c.post("/chat", json={"session_id": "t1", "message": "book me a seat",
                              "student_email": "s@mail.aub.edu"})
    assert r.status_code == 200
    assert r.json()["reply"] == "reply to book me a seat"
    assert seen[0]["session_id"] == "t1"
    assert seen[0]["student_email"] == "s@mail.aub.edu"


def test_the_email_is_optional(client):
    c, seen = client
    c.post("/chat", json={"session_id": "t1", "message": "hi"})
    assert seen[0]["student_email"] == ""


def test_the_signed_in_email_is_stated_once_and_not_renegotiated(monkeypatch):
    """ClassMate already authenticated the student, so Jafet should not ask for the email
    again -- and it should hear it once, not prepended to every message in the thread."""
    created, sent = [], []

    class _Sessions:
        def __init__(self):
            self.have = set()

        # keyed the way the real store is -- (app_name, user_id, session_id) -- so the
        # http and mcp callers cannot silently share one session id
        async def get_session(self, app_name, user_id, session_id):
            return object() if (user_id, session_id) in self.have else None

        async def create_session(self, app_name, user_id, session_id, state=None):
            created.append((session_id, state))
            self.have.add((user_id, session_id))

    class _Runner:
        session_service = _Sessions()

        async def run_async(self, user_id, session_id, new_message):
            sent.append(new_message.parts[0].text)
            return
            yield  # makes this an async generator

    runner = _Runner()
    monkeypatch.setattr(service, "runner", lambda: runner)

    import asyncio
    asyncio.run(service.run_turn("t1", "book a seat", student_email="s@mail.aub.edu"))
    asyncio.run(service.run_turn("t1", "tomorrow at 2", student_email="s@mail.aub.edu"))

    assert created == [("t1", {"student_email": "s@mail.aub.edu"})]
    assert "s@mail.aub.edu" in sent[0]
    assert sent[1] == "tomorrow at 2"


def test_a_non_aub_email_is_dropped_rather_than_asserted(monkeypatch):
    """ClassMate's backend only @Email-validates a signup, so a gmail account reaches here.
    Stating it would tell the model 'this is their AUB email, stop asking' and then have the
    booking guardrail reject it -- a dead end with no way back."""
    created, sent = [], []

    class _Sessions:
        def __init__(self):
            self.have = set()

        # keyed the way the real store is -- (app_name, user_id, session_id) -- so the
        # http and mcp callers cannot silently share one session id
        async def get_session(self, app_name, user_id, session_id):
            return object() if (user_id, session_id) in self.have else None

        async def create_session(self, app_name, user_id, session_id, state=None):
            created.append((session_id, state))
            self.have.add((user_id, session_id))

    class _Runner:
        session_service = _Sessions()

        async def run_async(self, user_id, session_id, new_message):
            sent.append(new_message.parts[0].text)
            return
            yield

    runner = _Runner()
    monkeypatch.setattr(service, "runner", lambda: runner)

    import asyncio
    asyncio.run(service.run_turn("t2", "book a seat", student_email="someone@gmail.com"))

    assert created == [("t2", {"student_email": ""})]
    assert sent[0] == "book a seat"


def test_http_and_mcp_do_not_share_a_session_id(monkeypatch):
    """The session store keys on user_id too, so "t1" over HTTP and "t1" over MCP are two
    conversations. Dropping the old shadow dict is what made that true -- it had them
    sharing one namespace and handing one caller the other's session."""
    created = []

    class _Sessions:
        def __init__(self):
            self.have = set()

        async def get_session(self, app_name, user_id, session_id):
            return object() if (user_id, session_id) in self.have else None

        async def create_session(self, app_name, user_id, session_id, state=None):
            created.append((user_id, session_id))
            self.have.add((user_id, session_id))

    class _Runner:
        session_service = _Sessions()

        async def run_async(self, user_id, session_id, new_message):
            return
            yield

    runner = _Runner()
    monkeypatch.setattr(service, "runner", lambda: runner)

    import asyncio
    asyncio.run(service.run_turn("t1", "hi", user_id="http"))
    asyncio.run(service.run_turn("t1", "hi", user_id="mcp"))
    asyncio.run(service.run_turn("t1", "again", user_id="http"))   # existing, not recreated

    assert created == [("http", "t1"), ("mcp", "t1")]
