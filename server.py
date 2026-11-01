"""JafetAI MCP toolkit: reserve a seat in AUB's Jafet Library Reference Reading Room.

Exactly three tools, one per category:

    find_seats    Data           reads LibCal's live availability grid, changes nothing
    book_seat     Action         reserves one seat segment (dry run unless LIVE_BOOKING=1)
    reserve_seat  Orchestration  find_seats -> pick the exact match -> book_seat, retrying
                                 the next seat if the first one was taken in between

Pure lookups that the agent does not need to "call" are resources, not tools:

    jafet://today                  today's date and the last bookable day
    jafet://availability/{day}     free-seat counts per slot for a day
    jafet://bookings/{email}       a student's booking history

Predictable failures (bad input, LibCal unreachable, slot taken) never fail silently:
bad input and an unreachable LibCal raise ToolError, which reaches the model as an
isError result with the message below; a taken slot is a normal "conflict" result.

Run: python server.py   (stdio; the agent in agent_client.py launches it for you)
"""

import logging
import sys
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

import requests
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from jafet import booking, db, scraper
from jafet.validation import student_problems

load_dotenv()

# stdout is the JSON-RPC channel, so every log line goes to stderr. The agent's terminal
# shows it, which is what makes the server side of the round trip visible in the demo.
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="  [jafet-server] %(message)s")
log = logging.getLogger("jafet")

mcp = FastMCP(
    "jafet",
    instructions="Seat reservations for AUB's Jafet Library Reference Reading Room. "
                 "Read jafet://today first to resolve relative dates.",
)

MAX_DAYS_AHEAD = 5
MAX_HOURS = 4


# ---- typed outputs ----

class Segment(BaseModel):
    seat_id: int
    seat_name: str
    start: str = Field(description="YYYY-MM-DD HH:MM:SS")
    end: str = Field(description="YYYY-MM-DD HH:MM:SS")


class SeatOption(BaseModel):
    kind: Literal["exact", "shifted", "shorter", "split"] = Field(
        description="exact = the requested window on one seat; shifted = same length at "
                    "another time; shorter = the biggest part of the window; split = the "
                    "full window hopping between seats")
    segments: list[Segment]


class SeatSearch(BaseModel):
    day: str
    requested_start: str | None
    requested_end: str | None
    note: str | None = Field(None, description="e.g. the time was snapped to the grid")
    options: list[SeatOption] = Field(description="exact options first; if there are "
                                                  "none, the alternatives")


class Booking(BaseModel):
    status: Literal["dry_run", "confirmed", "conflict"] = Field(
        description="dry_run = validated and recorded, not sent to LibCal; confirmed = "
                    "real LibCal reservation; conflict = the slot was taken, nothing booked")
    booking_id: int | None
    seat_id: int
    seat_name: str | None
    start: str
    end: str
    detail: str


class Reservation(BaseModel):
    status: Literal["reserved", "unavailable"] = Field(
        description="reserved = a seat was booked for the whole requested window; "
                    "unavailable = no seat was free for it, nothing was booked")
    booking: Booking | None
    alternatives: list[SeatOption] = Field(
        description="when unavailable: options the student could accept instead")
    seats_tried: int
    detail: str


# ---- helpers (not tools) ----

def _check_day(day: str) -> date:
    try:
        d = datetime.strptime(day.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ToolError(f"day must be a date in YYYY-MM-DD format, got {day!r}")
    today = date.today()
    if not today <= d <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise ToolError(f"{day} is not bookable: the room takes bookings from today "
                        f"({today}) up to {MAX_DAYS_AHEAD} days ahead "
                        f"({today + timedelta(days=MAX_DAYS_AHEAD)})")
    return d


def _libcal(call, *args):
    # LibCal being down or slow is the "unavailable resource" failure: say so plainly,
    # rather than letting a requests traceback reach the model
    try:
        return call(*args)
    except requests.RequestException as e:
        raise ToolError("LibCal (the library's booking system) is unreachable right "
                        f"now ({type(e).__name__}); nothing was booked, try again shortly")


def _seat_names() -> dict:
    names = db.cached_seats()
    if not names:
        names = _libcal(scraper.fetch_seat_names)
        db.cache_seats(names)
    return names


# ---- the three tools ----

@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def find_seats(
    day: Annotated[str, Field(description="Date, YYYY-MM-DD. Today up to 5 days ahead.")],
    start_time: Annotated[str, Field(description="Start time, HH:MM (24h). Snapped to "
                                                 "the nearest half-hour slot.")],
    hours: Annotated[int, Field(ge=1, le=MAX_HOURS,
                                description="Duration in whole hours, 1 to 4.")],
) -> SeatSearch:
    """Look up which Reading Room seats are free for a time window. Read-only.

    Returns exact options (one seat for the whole window) if there are any, otherwise
    alternatives: a shifted start, a shorter stay, or a split across seats."""
    log.info("find_seats(day=%s, start_time=%s, hours=%s)", day, start_time, hours)
    _check_day(day)
    try:
        datetime.strptime(start_time.strip(), "%H:%M")
    except ValueError:
        raise ToolError(f"start_time must be HH:MM (24h), got {start_time!r}")

    slots = scraper.parse_slots(_libcal(scraper.fetch_grid, day.strip()))
    if not slots:
        return SeatSearch(day=day, requested_start=None, requested_end=None,
                          note="LibCal has published no slots for this day "
                               "(the room may be closed)", options=[])
    plans = scraper.find_slot_plans(slots, _seat_names(), day.strip(),
                                    start_time.strip(), hours)
    return SeatSearch(day=day, requested_start=plans["requested"]["start"],
                      requested_end=plans["requested"]["end"], note=plans.get("note"),
                      options=[SeatOption(**o) for o in plans["options"]])


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                       "idempotentHint": False})
def book_seat(
    seat_id: Annotated[int, Field(description="seat_id from a find_seats option")],
    start: Annotated[str, Field(description="Segment start, YYYY-MM-DD HH:MM:SS, "
                                            "exactly as find_seats returned it")],
    end: Annotated[str, Field(description="Segment end, YYYY-MM-DD HH:MM:SS")],
    student_name: Annotated[str, Field(description="Student's full name")],
    student_id: Annotated[str, Field(description="AUB student ID, digits only")],
    email: Annotated[str, Field(description="AUB email ending in @mail.aub.edu")],
    program: Annotated[Literal["Undergraduate", "Graduate"],
                       Field(description="Student's program")],
) -> Booking:
    """Reserve ONE seat segment for a student. This changes something: it records a
    booking (a dry run unless the server runs with LIVE_BOOKING=1, in which case it
    is a real LibCal reservation).

    Re-checks the slot first. If someone took it in the meantime the result is
    status=conflict and nothing is booked."""
    log.info("book_seat(seat_id=%s, %s -> %s, email=%s)", seat_id, start, end, email)
    problems = student_problems(student_name, student_id, email, program)
    if problems:
        raise ToolError("cannot book, fix these first: " + "; ".join(problems))
    _check_day(start[:10])

    out = _libcal(booking.book, seat_id, start, end, student_name, student_id, email,
                  program)
    if out["status"] in ("error", "failed"):
        raise ToolError("booking rejected: " + out["detail"])
    if out["status"] == "conflict":
        return Booking(status="conflict", booking_id=None, seat_id=seat_id,
                       seat_name=None, start=start, end=end,
                       detail="taken since it was offered: " + ", ".join(out["taken_times"]))
    return Booking(status=out["status"], booking_id=out["booking_id"], seat_id=seat_id,
                   seat_name=out["seat_name"], start=out["start"], end=out["end"],
                   detail=out.get("detail") or "reservation confirmed by LibCal")


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                       "idempotentHint": False})
def reserve_seat(
    day: Annotated[str, Field(description="Date, YYYY-MM-DD. Today up to 5 days ahead.")],
    start_time: Annotated[str, Field(description="Start time, HH:MM (24h)")],
    hours: Annotated[int, Field(ge=1, le=MAX_HOURS, description="Duration, 1 to 4 hours")],
    student_name: Annotated[str, Field(description="Student's full name")],
    student_id: Annotated[str, Field(description="AUB student ID, digits only")],
    email: Annotated[str, Field(description="AUB email ending in @mail.aub.edu")],
    program: Annotated[Literal["Undergraduate", "Graduate"],
                       Field(description="Student's program")],
) -> Reservation:
    """Reserve a seat for a whole time window in one step: finds the free seats
    (find_seats), then books the first exact match (book_seat). If that seat was taken
    in the meantime it moves on to the next exact match, up to 3 seats.

    Only ever books an exact match. If no single seat is free for the whole window it
    books nothing and returns status=unavailable with the alternatives, so the student
    can choose one (then call reserve_seat again with the new time, or book_seat per
    segment for a split plan)."""
    log.info("reserve_seat(day=%s, start_time=%s, hours=%s) -> orchestrating",
             day, start_time, hours)
    # fail fast, before touching LibCal: a search would be wasted on details we
    # already know book_seat will refuse
    problems = student_problems(student_name, student_id, email, program)
    if problems:
        raise ToolError("cannot book, fix these first: " + "; ".join(problems))

    search = find_seats(day, start_time, hours)
    exact = [o for o in search.options if o.kind == "exact"]
    if not exact:
        return Reservation(status="unavailable", booking=None,
                           alternatives=search.options, seats_tried=0,
                           detail=search.note or "no single seat is free for the whole "
                                                 "window; nothing was booked")

    tried = 0
    for option in exact[:3]:
        seg = option.segments[0]
        tried += 1
        result = book_seat(seg.seat_id, seg.start, seg.end, student_name, student_id,
                           email, program)
        if result.status != "conflict":
            note = f" ({search.note})" if search.note else ""
            return Reservation(status="reserved", booking=result, alternatives=[],
                               seats_tried=tried,
                               detail=f"booked {seg.seat_name}{note}")
    return Reservation(status="unavailable", booking=None, alternatives=[],
                       seats_tried=tried,
                       detail="every free seat was taken while booking; nothing was "
                              "booked, search again")


# ---- resources: pure data lookups ----

@mcp.resource("jafet://today", mime_type="application/json")
def today_info() -> dict:
    """Today's date, so the agent can resolve 'tomorrow' or 'Friday'."""
    t = date.today()
    return {"today": t.isoformat(), "weekday": t.strftime("%A"),
            "last_bookable_day": (t + timedelta(days=MAX_DAYS_AHEAD)).isoformat()}


@mcp.resource("jafet://availability/{day}", mime_type="application/json")
def availability(day: str) -> dict:
    """Free seats per time slot for one day (YYYY-MM-DD)."""
    _check_day(day)
    slots = scraper.parse_slots(_libcal(scraper.fetch_grid, day))
    hours = {}
    for s in slots:
        h = hours.setdefault(s["start"], {"start": s["start"], "end": s["end"],
                                          "free_seats": 0})
        h["free_seats"] += s["available"]
    return {"date": day, "total_seats": len({s["seat_id"] for s in slots}),
            "slots": sorted(hours.values(), key=lambda h: h["start"])}


@mcp.resource("jafet://bookings/{email}", mime_type="application/json")
def bookings(email: str) -> dict:
    """Every booking this server has recorded for a student email."""
    return {"email": email, "bookings": db.bookings_for(email)}


if __name__ == "__main__":
    mcp.run(show_banner=False)  # stdio
