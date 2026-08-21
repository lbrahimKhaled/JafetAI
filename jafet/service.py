from google.adk.runners import InMemoryRunner
from google.genai import types

from . import guardrails
from .agent import root_agent

APP = "jafet"

_runner = None


def runner():
    # lazy so importing this module doesn't build the agent and its model: the tests
    # monkeypatch runner() and would otherwise pay for a real one in every process
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(agent=root_agent, app_name=APP)
    return _runner


def _preamble(student_email):
    # ClassMate has already authenticated the student, so their email is a fact rather than
    # something to negotiate for. This half only tells the model; the half that binds is the
    # session state below, which validate_booking_args checks -- a student can type this
    # exact bracket line themselves, so it can never be the thing that is trusted.
    return ("[the student is signed in as " + student_email + "; use this as their AUB "
            "email and do not ask for it again]\n")


async def run_turn(session_id: str, message: str, user_id: str = "http",
                   student_email: str = "") -> str:
    """One turn against a named session. Send the same session_id to continue a
    conversation -- that is what carries a half-finished booking across calls.

    Sessions live in memory: a restart drops them, and the student repeats themselves
    rather than answering into a session that is no longer there."""
    sessions = runner().session_service
    # asking the store beats keeping a shadow dict of what we think we created: they drift,
    # and create_session raises AlreadyExistsError on a session id the store still holds
    if await sessions.get_session(app_name=APP, user_id=user_id,
                                  session_id=session_id) is None:
        # ClassMate accounts are only @Email-validated by the backend, so a gmail address
        # reaches here. Passing one on would state an AUB email the booking guardrail then
        # rejects, right after telling the model not to ask for another -- a dead end.
        # Jafet owns the rule, so it is applied here rather than trusted from the caller.
        if not guardrails.EMAIL_RE.fullmatch(student_email):
            student_email = ""
        await sessions.create_session(app_name=APP, user_id=user_id, session_id=session_id,
                                      state={"student_email": student_email})
        if student_email:
            message = _preamble(student_email) + message

    content = types.Content(role="user", parts=[types.Part(text=message)])
    texts = []
    async for event in runner().run_async(user_id=user_id, session_id=session_id,
                                          new_message=content):
        if event.content and event.content.parts:
            # one event per model turn, and the earlier ones are tool-call rounds -- the
            # answer for the student is whatever the last one said
            parts = [p.text for p in event.content.parts if p.text and not p.thought]
            if parts:
                texts = parts
    return "\n".join(texts)


async def forget(session_id: str, user_id: str = "http") -> None:
    """Drop a session and everything negotiated in it. ClassMate calls this when a student
    deletes the chat: the negotiation holds their name, student ID and email, and a student
    who deletes that conversation has every reason to think those went with it."""
    await runner().session_service.delete_session(app_name=APP, user_id=user_id,
                                                  session_id=session_id)
