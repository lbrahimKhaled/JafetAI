import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

from jafet import booking, guardrails, scraper

DAY = (date.today() + timedelta(days=1)).isoformat()  # tomorrow, always future


def slot(seat, start_hm, end_hm, booked=False):
    s = {"start": DAY + " " + start_hm + ":00", "end": DAY + " " + end_hm + ":00",
         "itemId": seat, "checksum": "abc"}
    if booked:
        s["className"] = "s-lc-eq-checkout"
    return s


# real LibCal grid starts on the half hour - the fixture mirrors that
GRID = {"slots": [
    slot(1, "13:30", "14:30"),
    slot(1, "14:30", "15:30", booked=True),
    slot(1, "15:30", "16:30"),
    slot(2, "13:30", "14:30"),
    slot(2, "14:30", "15:30"),
    slot(2, "15:30", "16:30", booked=True),
]}

NAMES = {1: "Seat 001", 2: "Seat 002"}


def test_parse_slots():
    slots = scraper.parse_slots(GRID)
    assert len(slots) == 6
    assert slots[0]["available"] is True
    assert slots[1]["available"] is False
    assert slots[0]["seat_id"] == 1
    assert slots[0]["checksum"] == "abc"


def test_parse_empty_grid():
    assert scraper.parse_slots({}) == []
    assert scraper.parse_slots({"slots": []}) == []


def test_free_runs_merges_consecutive():
    slots = scraper.parse_slots(GRID)
    by_seat = scraper.slots_by_seat(slots)
    runs = scraper.free_runs(by_seat[2])
    assert runs == [(DAY + " 13:30:00", DAY + " 15:30:00")]


def test_exact_plan_found():
    slots = scraper.parse_slots(GRID)
    plans = scraper.find_slot_plans(slots, NAMES, DAY, "13:30", 1)
    assert plans["options"][0]["kind"] == "exact"
    assert "note" not in plans


def test_requested_time_snaps_to_grid():
    # 13:00 doesn't exist on the grid, must snap to 13:30 and say so
    slots = scraper.parse_slots(GRID)
    plans = scraper.find_slot_plans(slots, NAMES, DAY, "13:00", 1)
    assert plans["requested"]["start"] == DAY + " 13:30:00"
    assert "note" in plans
    assert plans["options"][0]["kind"] == "exact"
    assert plans["options"][0]["segments"][0]["start"] == DAY + " 13:30:00"


def test_split_plan_when_no_single_seat_works():
    # 3h window: seat 1 blocked mid, seat 2 blocked at the end -> hop seats
    slots = scraper.parse_slots(GRID)
    plans = scraper.find_slot_plans(slots, NAMES, DAY, "13:30", 3)
    kinds = [o["kind"] for o in plans["options"]]
    assert "exact" not in kinds
    assert "split" in kinds
    split = [o for o in plans["options"] if o["kind"] == "split"][0]
    assert split["segments"][0]["start"] == DAY + " 13:30:00"
    assert split["segments"][-1]["end"] == DAY + " 16:30:00"
    for a, b in zip(split["segments"], split["segments"][1:]):
        assert a["end"] == b["start"]


def test_no_split_plan_across_gap():
    # 14:30 slot missing entirely -> a "full coverage" split must not be offered
    gappy = {"slots": [s for s in GRID["slots"] if "14:30:00" not in s["start"]]}
    slots = scraper.parse_slots(gappy)
    plans = scraper.find_slot_plans(slots, NAMES, DAY, "13:30", 3)
    kinds = [o["kind"] for o in plans["options"]]
    assert "exact" not in kinds
    assert "split" not in kinds


def test_shifted_and_shorter_alternatives():
    slots = scraper.parse_slots(GRID)
    plans = scraper.find_slot_plans(slots, NAMES, DAY, "14:30", 2)
    kinds = [o["kind"] for o in plans["options"]]
    assert "shifted" in kinds or "shorter" in kinds


def test_empty_grid_plans():
    plans = scraper.find_slot_plans([], NAMES, DAY, "13:30", 2)
    assert plans["options"] == []
    assert "error" in plans


def test_seat_name_parsing():
    html = "resourceNameIdMap['eid_174554'] = 'Seat\\u0020002';"
    with patch("jafet.scraper.requests.get") as g:
        g.return_value.text = html
        g.return_value.raise_for_status = lambda: None
        names = scraper.fetch_seat_names()
    assert names == {174554: "Seat 002"}


def test_booking_conflict_detected():
    slots = scraper.parse_slots(GRID)
    wanted, taken = booking.slot_conflicts(slots, 1, DAY + " 13:30:00", DAY + " 16:30:00")
    assert taken == [DAY + " 14:30:00"]


def test_booking_rejects_gapped_window():
    gappy = {"slots": [s for s in GRID["slots"] if "14:30:00" not in s["start"]]}
    slots = scraper.parse_slots(gappy)
    wanted, taken = booking.slot_conflicts(slots, 1, DAY + " 13:30:00", DAY + " 16:30:00")
    assert wanted is None


def test_booking_rejects_misaligned_window():
    slots = scraper.parse_slots(GRID)
    wanted, taken = booking.slot_conflicts(slots, 1, DAY + " 13:00:00", DAY + " 14:00:00")
    assert wanted is None


def test_booking_unknown_seat():
    slots = scraper.parse_slots(GRID)
    wanted, taken = booking.slot_conflicts(slots, 99, DAY + " 13:30:00", DAY + " 14:30:00")
    assert wanted is None


def test_dry_run_never_posts():
    with patch("jafet.scraper.fetch_grid", return_value=GRID), \
         patch("jafet.booking.requests.Session") as sess:
        out = booking.book(1, DAY + " 13:30:00", DAY + " 14:30:00",
                           "Test User", "202400000", "tu00@mail.aub.edu")
    assert out["status"] == "dry_run"
    sess.assert_not_called()


def test_book_rejects_over_four_hours():
    out = booking.book(1, DAY + " 08:30:00", DAY + " 16:30:00",
                       "Test User", "202400000", "tu00@mail.aub.edu")
    assert out["status"] == "error"


def test_book_rejects_far_future():
    far = (date.today() + timedelta(days=14)).isoformat()
    out = booking.book(1, far + " 13:30:00", far + " 14:30:00",
                       "Test User", "202400000", "tu00@mail.aub.edu")
    assert out["status"] == "error"


def test_book_rejects_garbage_input():
    out = booking.book("seat one", "tomorrow", "later", "x", "1", "a@mail.aub.edu")
    assert out["status"] == "error"


class FakeTool:
    name = "book_seat"


class FakeCtx:
    # ADK always hands the callback a context; state is where a signed-in caller's
    # identity lives, and an empty one is the walk-in case
    def __init__(self, state=None):
        self.state = state or {}


def test_guardrail_rejects_bad_ids_and_emails():
    base = {"student_name": "Test User", "student_id": "202400000",
            "email": "tu00@mail.aub.edu"}
    assert guardrails.validate_booking_args(FakeTool(), base, FakeCtx()) is None
    for bad in [{"email": "tu@gmail.com"}, {"email": "tu00@mail.aub.edu\n"},
                {"student_id": "٢٠٢٣٠٠٠٠٠"}, {"student_id": "²²²²²²"},
                {"student_id": "AB12345"}, {"student_name": "  "}]:
        args = dict(base)
        args.update(bad)
        out = guardrails.validate_booking_args(FakeTool(), args, FakeCtx())
        assert out is not None and out["status"] == "rejected", str(bad)


def test_a_signed_in_student_cannot_book_as_someone_else():
    """The domain check alone lets one valid AUB address stand in for another, which spends
    a real student's daily quota. Session state is the only thing here the student cannot
    type, so it is what the check reads."""
    ctx = FakeCtx({"student_email": "tu00@mail.aub.edu"})
    mine = {"student_name": "Test User", "student_id": "202400000",
            "email": "tu00@mail.aub.edu"}
    assert guardrails.validate_booking_args(FakeTool(), mine, ctx) is None

    theirs = dict(mine, email="victim@mail.aub.edu")
    out = guardrails.validate_booking_args(FakeTool(), theirs, ctx)
    assert out is not None and out["status"] == "rejected"

    # a walk-in session states no identity, so it still negotiates its own email
    assert guardrails.validate_booking_args(FakeTool(), theirs, FakeCtx()) is None


class FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.posts = []
        self.cart = []

    def get(self, url, timeout=None):
        return FakeResp(text="")

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url, data))
        if url.endswith("/booking/add"):
            # every add returns the whole cart; one entry per grid slot, each
            # one slot long with its own checksum (real 3h payload, 2026-08-21)
            began = datetime.strptime(data["add[start]"], "%Y-%m-%d %H:%M")
            self.cart.append({
                "id": len(self.cart) + 1, "eid": 153161,
                "seat_id": int(data["add[seat_id]"]), "gid": 38312, "lid": 18083,
                "start": began.strftime(scraper.FMT),
                "end": (began + timedelta(hours=1)).strftime(scraper.FMT),
                "checksum": "c" + str(len(self.cart) + 1)})
            return FakeResp(payload={"bookings": list(self.cart)})
        if url.endswith("/space/times"):
            # mirrors the real page: a Zip honeypot lives OUTSIDE the booking
            # form, the real fields live inside #s-lc-eq-bform
            return FakeResp(payload={"html":
                '<input id="slcrh-Zip" name="Zip" value="" type="text" required>'
                '<form id="s-lc-eq-bform" action="/ajax/space/book">'
                '<input type="hidden" name="session" value="12345">'
                '<input type="text" name="q22184" required>'
                '<input type="radio" name="q22314" value="Undergraduate">'
                '<input type="radio" name="q22314" value="Graduate">'
                '</form>'})
        return FakeResp(payload=None, text='{"bookId": 999}')


def test_live_flow_posts_real_libcal_shape(monkeypatch):
    monkeypatch.setenv("LIVE_BOOKING", "1")
    monkeypatch.setattr(booking, "_session", None)
    fake = FakeSession()
    with patch("jafet.scraper.fetch_grid", return_value=GRID), \
         patch("jafet.booking.requests.Session", return_value=fake):
        out = booking.book(2, DAY + " 13:30:00", DAY + " 15:30:00",
                           "Test User", "202400000", "tu00@mail.aub.edu")
    assert out["status"] == "confirmed"
    # a 2h window is two one-slot cart adds, not one add with a longer end
    add_url, add_data = fake.posts[0]
    assert add_data["add[start]"] == DAY + " 13:30"
    assert "add[end]" not in add_data
    assert "bookings[0][id]" not in add_data  # cart is empty on the first add
    second_url, second_data = fake.posts[1]
    assert second_url.endswith("/booking/add")
    assert second_data["add[start]"] == DAY + " 14:30"
    # the second add echoes the first entry back
    assert second_data["bookings[0][checksum]"] == "c1"
    times_url, times_data = fake.posts[2]
    assert times_data["bookings[0][start]"] == DAY + " 13:30"
    assert times_data["bookings[0][checksum]"] == "c1"
    assert times_data["bookings[1][start]"] == DAY + " 14:30"
    assert times_data["bookings[1][checksum]"] == "c2"
    book_url, book_data = fake.posts[3]
    assert book_data["session"] == "12345"
    assert book_data["q22184"] == "202400000"
    assert book_data["fname"] == "Test" and book_data["lname"] == "User"
    # the Zip honeypot lives outside the booking form and must NOT be sent
    assert "Zip" not in book_data
    assert book_data["q22314"] == "Undergraduate"
    # /book needs the bookings JSON + returnUrl + pickupHolds + method (real payload)
    assert book_data["method"] == 11
    assert book_data["returnUrl"] == "/reserve/spaces/rsasr"
    assert book_data["pickupHolds"] == ""
    # both slots go to /book as separate entries, mirroring the real payload
    booked = json.loads(book_data["bookings"])
    assert len(booked) == 2
    assert booked[0]["id"] == 1 and booked[0]["checksum"] == "c1"
    assert booked[0]["seat_id"] == 2 and booked[0]["end"] == DAY + " 14:30"
    assert booked[1]["id"] == 2 and booked[1]["checksum"] == "c2"
    assert booked[1]["start"] == DAY + " 14:30" and booked[1]["end"] == DAY + " 15:30"
