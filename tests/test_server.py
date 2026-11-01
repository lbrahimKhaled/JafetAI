"""The three MCP tools and the resources, called the way an agent calls them: through
an MCP client (FastMCP's in-memory transport), so argument validation, ToolError ->
isError and structured output are all exercised, not just the Python functions.

LibCal is mocked: no network, no key, nothing is ever booked."""

import asyncio
import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest
import requests
from fastmcp import Client

import server
from jafet import db

DAY = (date.today() + timedelta(days=1)).isoformat()
STUDENT = {"student_name": "Test User", "student_id": "202400000",
           "email": "tu00@mail.aub.edu", "program": "Undergraduate"}


def slot(seat, start_hm, end_hm, booked=False):
    s = {"start": f"{DAY} {start_hm}:00", "end": f"{DAY} {end_hm}:00",
         "itemId": seat, "checksum": "abc"}
    if booked:
        s["className"] = "s-lc-eq-checkout"
    return s


# seat 1 is free 13:30-15:30, seat 2 is free 13:30-14:30 then booked
GRID = {"slots": [
    slot(1, "13:30", "14:30"), slot(1, "14:30", "15:30"),
    slot(2, "13:30", "14:30"), slot(2, "14:30", "15:30", booked=True),
]}
# nobody has 13:30-15:30 on one seat, but hopping seats covers it
SPLIT_GRID = {"slots": [
    slot(1, "13:30", "14:30"), slot(1, "14:30", "15:30", booked=True),
    slot(2, "13:30", "14:30", booked=True), slot(2, "14:30", "15:30"),
]}
NAMES = {1: "Seat 001", 2: "Seat 002"}


def call(tool, args, grid=GRID):
    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool(tool, args, raise_on_error=False)
    with patch("jafet.scraper.fetch_grid", return_value=grid), \
            patch("jafet.scraper.fetch_seat_names", return_value=NAMES):
        return asyncio.run(go())


def read(uri, grid=GRID):
    async def go():
        async with Client(server.mcp) as c:
            return await c.read_resource(uri)
    with patch("jafet.scraper.fetch_grid", return_value=grid):
        return json.loads(asyncio.run(go())[0].text)


def text(result):
    return result.content[0].text


# ---- the toolkit itself ----

def test_exactly_three_tools_one_per_category():
    async def go():
        async with Client(server.mcp) as c:
            return {t.name: t for t in await c.list_tools()}
    tools = asyncio.run(go())
    assert set(tools) == {"find_seats", "book_seat", "reserve_seat"}
    assert tools["find_seats"].annotations.read_only_hint is True     # Data
    assert tools["book_seat"].annotations.read_only_hint is False     # Action
    assert tools["reserve_seat"].annotations.read_only_hint is False  # Orchestration
    for t in tools.values():
        assert t.description and t.output_schema, t.name


# ---- Data: find_seats ----

def test_find_seats_returns_exact_option():
    r = call("find_seats", {"day": DAY, "start_time": "13:30", "hours": 2})
    assert not r.is_error
    out = r.structured_content
    assert [o["kind"] for o in out["options"]] == ["exact"]
    assert out["options"][0]["segments"][0]["seat_name"] == "Seat 001"


def test_find_seats_snaps_to_grid_and_says_so():
    out = call("find_seats", {"day": DAY, "start_time": "13:00", "hours": 1}).structured_content
    assert out["requested_start"] == f"{DAY} 13:30:00"
    assert "snapped" in out["note"]


def test_find_seats_offers_alternatives_when_no_exact():
    out = call("find_seats", {"day": DAY, "start_time": "13:30", "hours": 2},
               grid=SPLIT_GRID).structured_content
    kinds = {o["kind"] for o in out["options"]}
    assert "exact" not in kinds and "split" in kinds


def test_find_seats_changes_nothing():
    call("find_seats", {"day": DAY, "start_time": "13:30", "hours": 2})
    assert db.bookings_for(STUDENT["email"]) == []


@pytest.mark.parametrize("args, expected", [
    ({"day": "next friday", "start_time": "13:30", "hours": 2}, "YYYY-MM-DD"),
    ({"day": "2020-01-01", "start_time": "13:30", "hours": 2}, "not bookable"),
    ({"day": DAY, "start_time": "1:30pm", "hours": 2}, "HH:MM"),
    ({"day": DAY, "start_time": "13:30", "hours": 6}, "less than or equal to 4"),
])
def test_find_seats_rejects_bad_input_clearly(args, expected):
    r = call("find_seats", args)
    assert r.is_error
    assert expected in text(r)


def test_find_seats_reports_libcal_down():
    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool("find_seats", {"day": DAY, "start_time": "13:30",
                                                    "hours": 1}, raise_on_error=False)
    with patch("jafet.scraper.fetch_grid", side_effect=requests.ConnectionError("down")):
        r = asyncio.run(go())
    assert r.is_error
    assert "LibCal" in text(r) and "unreachable" in text(r)


# ---- Action: book_seat ----

def segment(seat=1, start="13:30", end="15:30"):
    return {"seat_id": seat, "start": f"{DAY} {start}:00", "end": f"{DAY} {end}:00"}


def test_book_seat_dry_run_records_booking():
    r = call("book_seat", {**segment(), **STUDENT})
    assert not r.is_error
    assert r.structured_content["status"] == "dry_run"
    assert len(db.bookings_for(STUDENT["email"])) == 1


def test_book_seat_conflict_books_nothing():
    r = call("book_seat", {**segment(seat=2), **STUDENT})
    assert r.structured_content["status"] == "conflict"
    assert db.bookings_for(STUDENT["email"]) == []


@pytest.mark.parametrize("bad, expected", [
    ({"email": "tu00@gmail.com"}, "@mail.aub.edu"),
    ({"student_id": "AB123"}, "digits"),
    ({"student_name": " "}, "student_name"),
])
def test_book_seat_rejects_bad_student_details(bad, expected):
    r = call("book_seat", {**segment(), **STUDENT, **bad})
    assert r.is_error and expected in text(r)
    assert db.bookings_for(STUDENT["email"]) == []


def test_book_seat_rejects_unknown_program_at_the_schema():
    r = call("book_seat", {**segment(), **STUDENT, "program": "PhD"})
    assert r.is_error


# ---- Orchestration: reserve_seat ----

def test_reserve_seat_finds_then_books():
    r = call("reserve_seat", {"day": DAY, "start_time": "13:30", "hours": 2, **STUDENT})
    out = r.structured_content
    assert out["status"] == "reserved"
    assert out["booking"]["status"] == "dry_run"
    assert out["booking"]["seat_name"] == "Seat 001"
    assert len(db.bookings_for(STUDENT["email"])) == 1


def test_reserve_seat_never_books_an_alternative_on_its_own():
    r = call("reserve_seat", {"day": DAY, "start_time": "13:30", "hours": 2, **STUDENT},
             grid=SPLIT_GRID)
    out = r.structured_content
    assert out["status"] == "unavailable"
    assert out["alternatives"]
    assert db.bookings_for(STUDENT["email"]) == []


def test_reserve_seat_moves_on_when_first_seat_is_taken_meanwhile():
    # both seats free when searched; seat 1 gets taken before book_seat re-checks it
    free = {"slots": [slot(1, "13:30", "14:30"), slot(2, "13:30", "14:30")]}
    taken = {"slots": [slot(1, "13:30", "14:30", booked=True), slot(2, "13:30", "14:30")]}

    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool("reserve_seat", {"day": DAY, "start_time": "13:30",
                                                      "hours": 1, **STUDENT})
    with patch("jafet.scraper.fetch_grid", side_effect=[free, taken, taken]), \
            patch("jafet.scraper.fetch_seat_names", return_value=NAMES):
        out = asyncio.run(go()).structured_content
    assert out["status"] == "reserved"
    assert out["seats_tried"] == 2
    assert out["booking"]["seat_id"] == 2


def test_reserve_seat_validates_before_touching_libcal():
    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool("reserve_seat", {
                "day": DAY, "start_time": "13:30", "hours": 2,
                **STUDENT, "email": "someone@gmail.com"}, raise_on_error=False)
    with patch("jafet.scraper.fetch_grid") as grid:
        r = asyncio.run(go())
    assert r.is_error and "@mail.aub.edu" in text(r)
    grid.assert_not_called()


# ---- resources ----

def test_today_resource():
    out = read("jafet://today")
    assert out["today"] == date.today().isoformat()


def test_availability_resource_counts_free_seats():
    out = read(f"jafet://availability/{DAY}")
    assert out["total_seats"] == 2
    assert [s["free_seats"] for s in out["slots"]] == [2, 1]


def test_bookings_resource_shows_history():
    call("book_seat", {**segment(), **STUDENT})
    out = read(f"jafet://bookings/{STUDENT['email']}")
    assert len(out["bookings"]) == 1 and out["bookings"][0]["status"] == "dry_run"
