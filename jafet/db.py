import os
import sqlite3
from datetime import datetime


def db_path():
    return os.environ.get("JAFET_DB",
                          os.path.join(os.path.dirname(__file__), "..", "jafet.db"))


def conn():
    c = sqlite3.connect(db_path())
    c.execute("create table if not exists seats(seat_id integer primary key, name text)")
    c.execute("create table if not exists bookings("
              "id integer primary key autoincrement,"
              "student_name text, student_id text, email text,"
              "seat_id integer, seat_name text,"
              "start_ts text, end_ts text, status text, created_at text)")
    c.execute("create index if not exists idx_bookings_email on bookings(email)")
    return c


def cache_seats(names):
    c = conn()
    c.executemany("insert or replace into seats(seat_id, name) values(?,?)",
                  list(names.items()))
    c.commit()
    c.close()


def cached_seats():
    c = conn()
    rows = c.execute("select seat_id, name from seats").fetchall()
    c.close()
    return dict(rows)


def save_booking(student_name, student_id, email, seat_id, seat_name, start, end, status):
    c = conn()
    cur = c.execute("insert into bookings(student_name, student_id, email, seat_id,"
                    "seat_name, start_ts, end_ts, status, created_at) values(?,?,?,?,?,?,?,?,?)",
                    (student_name, student_id, email, seat_id, seat_name, start, end,
                     status, datetime.now().isoformat()))
    c.commit()
    bid = cur.lastrowid
    c.close()
    return bid


def bookings_for(email):
    c = conn()
    rows = c.execute("select id, seat_name, start_ts, end_ts, status from bookings "
                     "where email = ? order by id desc", (email,)).fetchall()
    c.close()
    return [{"booking_id": r[0], "seat_name": r[1], "start": r[2], "end": r[3],
             "status": r[4]} for r in rows]
