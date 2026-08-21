import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from jafet import service

# One POST per turn, against a session the caller names. The ADK api_server can do this
# too, but only as create-session-then-run against its own URL shape; ClassMate needs one
# stable endpoint it can call from inside its own tool.
app = FastAPI(title="JafetAI")


class ChatIn(BaseModel):
    session_id: str
    message: str
    # the caller has already authenticated the student, so Jafet can skip asking for it
    student_email: str = ""


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/chat")
async def chat(body: ChatIn) -> dict:
    reply = await service.run_turn(body.session_id, body.message,
                                   user_id="http", student_email=body.student_email)
    return {"reply": reply}


@app.delete("/session/{session_id}")
async def forget_session(session_id: str) -> dict:
    await service.forget(session_id)
    return {"ok": True}


if __name__ == "__main__":
    # loopback by default: this endpoint reaches the booking tools, so it is not something
    # to publish without deciding to
    uvicorn.run(app, host=os.environ.get("JAFET_HTTP_HOST", "127.0.0.1"),
                port=int(os.environ.get("JAFET_HTTP_PORT", "8802")))
