import json
import os
import re
from datetime import date, datetime, timedelta

import requests

from . import db, scraper


def slot_conflicts(slots, seat_id, start, end):
    # the window must map exactly onto contiguous grid slots - no partial
    # slots, no holes - and every one of them must be free
    wanted = [s for s in slots if s["seat_id"] == seat_id and start <= s["start"] < end]
    if not wanted:
        return None, "seat has no slots in that window"
    wanted.sort(key=lambda s: s["start"])
    if wanted[0]["start"] != start or wanted[-1]["end"] != end:
        return None, "window does not line up with the booking grid"
    for a, b in zip(wanted, wanted[1:]):
        if a["end"] != b["start"]:
            return None, "window has a gap in the booking grid"
    taken = [s["start"] for s in wanted if not s["available"]]
    return wanted, taken


_session = None


def _get_session():
    # one shared cookie session: retries then reuse our own cart instead of
    # colliding with the 5-minute hold a previous attempt left behind
    global _session
    if _session is None:
        _session = requests.Session()
        _session.get(scraper.RESERVE_URL, timeout=20)
    return _session


def cart_fields(cart):
    # every call echoes the current cart back as a bookings[i][...] form array
    fields = {}
    for i, b in enumerate(cart):
        p = "bookings[" + str(i) + "]"
        fields[p + "[id]"] = b["id"]
        fields[p + "[eid]"] = b["eid"]
        fields[p + "[seat_id]"] = b["seat_id"]
        fields[p + "[gid]"] = b["gid"]
        fields[p + "[lid]"] = b["lid"]
        fields[p + "[start]"] = b["start"][:16]
        fields[p + "[end]"] = b["end"][:16]
        fields[p + "[checksum]"] = b["checksum"]
    return fields


def submit_live(seat_id, window, student_name, student_id, email,
                program="Undergraduate"):
    # real LibCal flow, captured from the live site 2026-08-21: one cart add per
    # grid slot - a 3 hour booking is three one-slot entries, each with its own
    # checksum, not one entry with a longer end -> submit times (session + form)
    # -> submit booking
    headers = {"Referer": scraper.RESERVE_URL,
               "X-Requested-With": "XMLHttpRequest"}
    s = _get_session()

    day = window[0]["start"][:10]
    next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    cart = []
    for i, sl in enumerate(window):
        add = {
            "add[eid]": scraper.EID, "add[seat_id]": seat_id, "add[gid]": scraper.GID,
            "add[lid]": scraper.LID, "add[start]": sl["start"][:16],
            "add[checksum]": sl["checksum"],
            "lid": scraper.LID, "gid": scraper.GID, "start": day, "end": next_day,
        }
        add.update(cart_fields(cart))
        r = s.post(scraper.BASE + "/spaces/availability/booking/add", data=add,
                   headers=headers, timeout=20)
        if r.status_code != 200:
            return {"ok": False, "error": "cart add failed: HTTP " + str(r.status_code)
                    + " " + r.text[:300]}
        try:
            data = r.json()
        except ValueError:
            data = {}
        cart = data.get("bookings") or []
        if len(cart) != i + 1:
            return {"ok": False, "error": "cart add rejected at " + sl["start"]
                    + " (slot probably taken): " + r.text[:300]}

    # /times takes the cart as a form array, /book takes the same entries as a
    # JSON string - both carry every entry's id + checksum (captured 2026-08-21)
    times = {
        "patron": "", "patronHash": "", "returnUrl": "/reserve/spaces/rsasr",
        "method": 11,
    }
    times.update(cart_fields(cart))
    r2 = s.post(scraper.BASE + "/ajax/space/times", data=times,
                headers=headers, timeout=20)
    if r2.status_code != 200:
        return {"ok": False, "error": "times submit failed: HTTP " + str(r2.status_code)
                + " " + r2.text[:300]}
    html = r2.json().get("html", "")

    m = re.search(r'name="session"[^>]*value="(\d+)"', html)
    if m is None:
        m = re.search(r'value="(\d+)"[^>]*name="session"', html)
    if m is None:
        return {"ok": False, "error": "no session id in LibCal checkout form"}

    # only the real booking form (#s-lc-eq-bform) gets submitted - other page
    # sections carry a Zip honeypot that breaks the POST if included
    bform = re.search(r'id="s-lc-eq-bform".*?</form>', html, re.S)
    bhtml = bform.group(0) if bform else html

    parts = student_name.split(None, 1)
    booked = [{"id": b["id"], "eid": b["eid"], "seat_id": b["seat_id"],
               "gid": b["gid"], "lid": b["lid"], "start": b["start"][:16],
               "end": b["end"][:16], "checksum": b["checksum"]} for b in cart]
    # exact shape of a real submit (captured 2026-08-21): the booking form
    # fields plus bookings JSON, returnUrl, empty pickupHolds, method
    form = {
        "session": m.group(1),
        "fname": parts[0],
        "lname": parts[1] if len(parts) > 1 else "",
        "email": email,
        "bookings": json.dumps(booked),
        "returnUrl": "/reserve/spaces/rsasr",
        "pickupHolds": "",
        "method": 11,
    }
    # the required qNNNN text question is the AUB ID (q22184 today, parsed so a
    # renumbering doesn't break us); the program radio (also qNNNN) is picked
    # to match the student
    for tag in re.findall(r"<input[^>]+>", bhtml):
        name = re.search(r'name="(q\d+)"', tag)
        if name is None:
            continue
        n = name.group(1)
        if 'type="radio"' in tag:
            value = re.search(r'value="([^"]*)"', tag)
            if value and value.group(1).lower() == program.lower():
                form[n] = value.group(1)
        elif 'type="text"' in tag:
            form[n] = student_id
    r3 = s.post(scraper.BASE + "/ajax/space/book", data=form,
                headers=headers, timeout=20)
    if r3.status_code != 200:
        return {"ok": False, "error": "booking submit failed: HTTP " + str(r3.status_code)
                + " " + r3.text[:300]}
    text = r3.text
    # LibCal reports form problems in the response (the red text under submit)
    if "bookId" in text or "success" in text.lower():
        return {"ok": True, "receipt": text[:500]}
    if "error" in text.lower():
        return {"ok": False, "error": text[:500]}
    return {"ok": True, "receipt": text[:500]}


def book(seat_id, start, end, student_name, student_id, email, program="Undergraduate"):
    try:
        seat_id = int(seat_id)
        s_dt = datetime.strptime(start, scraper.FMT)
        e_dt = datetime.strptime(end, scraper.FMT)
    except ValueError:
        return {"status": "error",
                "detail": "bad seat_id or times (want YYYY-MM-DD HH:MM:SS)"}
    if e_dt <= s_dt or e_dt - s_dt > timedelta(hours=4):
        return {"status": "error", "detail": "a booking is at most 4 hours"}
    if s_dt < datetime.now():
        return {"status": "error", "detail": "that start time has already passed"}
    today = date.today()
    if not (today <= s_dt.date() <= today + timedelta(days=5)):
        return {"status": "error", "detail": "bookings only today through 5 days ahead"}

    grid = scraper.fetch_grid(start[:10])
    slots = scraper.parse_slots(grid)
    names = db.cached_seats()
    seat_name = names.get(seat_id, "seat " + str(seat_id))

    if os.environ.get("LIVE_BOOKING") != "1":
        wanted, taken = slot_conflicts(slots, seat_id, start, end)
        if wanted is None:
            return {"status": "error", "detail": taken}
        if taken:
            return {"status": "conflict", "taken_times": taken,
                    "detail": "slot(s) no longer available, pick another option"}
        bid = db.save_booking(student_name, student_id, email, seat_id, seat_name,
                              start, end, "dry_run")
        return {"status": "dry_run", "booking_id": bid, "seat_name": seat_name,
                "start": start, "end": end,
                "detail": "DRY RUN - validated but not submitted to LibCal"}

    # live: book directly and let LibCal be the judge - a pre-check would see
    # our own pending 5-minute holds and wrongly call the slot taken
    window = sorted([s for s in slots if s["seat_id"] == seat_id
                     and start <= s["start"] < end], key=lambda s: s["start"])
    if not window:
        return {"status": "error",
                "detail": "no slot starts at " + start + " for that seat"}
    if window[0]["start"] != start or window[-1]["end"] != end:
        return {"status": "error",
                "detail": "window does not line up with the booking grid"}
    out = submit_live(seat_id, window, student_name, student_id, email, program)
    if not out["ok"]:
        db.save_booking(student_name, student_id, email, seat_id, seat_name,
                        start, end, "failed")
        return {"status": "failed", "detail": out["error"]}
    bid = db.save_booking(student_name, student_id, email, seat_id, seat_name,
                          start, end, "confirmed")
    return {"status": "confirmed", "booking_id": bid, "seat_name": seat_name,
            "start": start, "end": end, "libcal_receipt": out["receipt"]}
