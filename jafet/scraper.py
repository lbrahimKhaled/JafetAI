import re
from datetime import datetime, timedelta

import requests

BASE = "https://aub.edu.lb.libcal.com"
RESERVE_URL = BASE + "/reserve/spaces/rsasr"
LID = 18083
GID = 38312
EID = 153161  # the Reference Reading Room space, shared by all seats

FMT = "%Y-%m-%d %H:%M:%S"


def fetch_grid(date):
    end = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    data = {
        "lid": LID, "gid": GID, "eid": -1, "seat": 1, "seatId": 0, "zone": 0,
        "start": date, "end": end, "pageIndex": 0, "pageSize": 500,
    }
    r = requests.post(BASE + "/spaces/availability/grid", data=data,
                      headers={"Referer": RESERVE_URL}, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_slots(grid):
    # a slot with className (s-lc-eq-checkout) is already booked
    slots = []
    for s in grid.get("slots", []):
        slots.append({
            "seat_id": s["itemId"],
            "start": s["start"],
            "end": s["end"],
            "available": "className" not in s,
            "checksum": s.get("checksum", ""),
        })
    return slots


def fetch_seat_names():
    # seat names live in the reserve page's embedded JS, not in the grid JSON
    r = requests.get(RESERVE_URL, timeout=20)
    r.raise_for_status()
    pairs = re.findall(r"resourceNameIdMap\['eid_(\d+)'\]\s*=\s*'([^']*)'", r.text)
    names = {}
    for sid, raw in pairs:
        names[int(sid)] = raw.encode().decode("unicode_escape")
    return names


def slots_by_seat(slots):
    by_seat = {}
    for s in slots:
        by_seat.setdefault(s["seat_id"], []).append(s)
    for sid in by_seat:
        by_seat[sid].sort(key=lambda s: s["start"])
    return by_seat


def free_runs(seat_slots):
    # merge consecutive available slots into (start, end) runs
    runs = []
    cur = None
    for s in seat_slots:
        if not s["available"]:
            cur = None
            continue
        if cur is not None and cur[1] == s["start"]:
            cur[1] = s["end"]
            continue
        cur = [s["start"], s["end"]]
        runs.append(cur)
    return [(r[0], r[1]) for r in runs]


def _dt(s):
    return datetime.strptime(s, FMT)


def find_slot_plans(slots, seat_names, date, start_time, hours):
    """Rank booking options for the requested window: exact seats first,
    otherwise shifted / shorter / split-across-seats alternatives."""
    grid_starts = sorted(set(s["start"] for s in slots))
    if not grid_starts:
        return {"error": "no slots published for " + date, "options": []}

    # user times rarely sit on the grid (slots start on the half hour) - snap
    # to the nearest real slot start so plans always match bookable slots
    asked = date + " " + start_time + ":00"
    want_start = min(grid_starts,
                     key=lambda st: abs((_dt(st) - _dt(asked)).total_seconds()))
    want_end = (_dt(want_start) + timedelta(hours=hours)).strftime(FMT)
    by_seat = slots_by_seat(slots)

    def name(sid):
        return seat_names.get(sid, "seat " + str(sid))

    result = {"requested": {"start": want_start, "end": want_end}}
    if want_start != asked:
        result["note"] = ("requested time snapped to the booking grid "
                          "(slots start at " + want_start[11:16] + ")")

    exact = []
    for sid, seat_slots in by_seat.items():
        for rs, re_ in free_runs(seat_slots):
            if rs <= want_start and re_ >= want_end:
                exact.append({"kind": "exact", "segments": [
                    {"seat_id": sid, "seat_name": name(sid), "start": want_start, "end": want_end}]})
                break
    if exact:
        result["options"] = exact[:5]
        return result

    alternatives = []

    # shifted: same duration on one seat, closest start to what was asked
    shifted = []
    for sid, seat_slots in by_seat.items():
        starts = [s["start"] for s in seat_slots if s["available"]]
        for rs, re_ in free_runs(seat_slots):
            if _dt(re_) - _dt(rs) < timedelta(hours=hours):
                continue
            for st in starts:
                if st < rs or st > re_:
                    continue
                en = (_dt(st) + timedelta(hours=hours)).strftime(FMT)
                if en > re_:
                    continue
                dist = abs((_dt(st) - _dt(want_start)).total_seconds())
                shifted.append((dist, sid, st, en))
    shifted.sort(key=lambda x: x[0])
    seen = set()
    for dist, sid, st, en in shifted:
        if st in seen:
            continue
        seen.add(st)
        alternatives.append({"kind": "shifted", "segments": [
            {"seat_id": sid, "seat_name": name(sid), "start": st, "end": en}]})
        if len(seen) == 3:
            break

    # shorter: biggest overlap with the requested window on one seat
    best = None
    for sid, seat_slots in by_seat.items():
        for rs, re_ in free_runs(seat_slots):
            os_ = max(rs, want_start)
            oe = min(re_, want_end)
            if os_ >= oe:
                continue
            length = (_dt(oe) - _dt(os_)).total_seconds()
            if best is None or length > best[0]:
                best = (length, sid, os_, oe)
    if best is not None:
        alternatives.append({"kind": "shorter", "segments": [
            {"seat_id": best[1], "seat_name": name(best[1]), "start": best[2], "end": best[3]}]})

    # split: cover the full window by hopping seats, staying put when possible
    segments = []
    cursor = want_start
    covered = True
    while cursor < want_end:
        options = [s for s in slots if s["start"] == cursor and s["available"]]
        pick = None
        for o in options:
            if segments and o["seat_id"] == segments[-1]["seat_id"]:
                pick = o
                break
        if pick is None and options:
            pick = options[0]
        if pick is None:
            covered = False
            break
        if segments and pick["seat_id"] == segments[-1]["seat_id"]:
            segments[-1]["end"] = pick["end"]
        else:
            segments.append({"seat_id": pick["seat_id"], "seat_name": name(pick["seat_id"]),
                             "start": pick["start"], "end": pick["end"]})
        cursor = pick["end"]
    if covered and len(segments) > 1:
        alternatives.append({"kind": "split", "segments": segments})

    result["options"] = alternatives
    return result
