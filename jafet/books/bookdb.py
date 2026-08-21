import os

import psycopg

COLS = ["mms_id", "title", "author", "isbn", "publisher", "place", "edition", "year",
        "language", "format", "subjects", "call_number", "library", "location",
        "availability_status", "description"]


def dsn():
    return os.environ.get("JAFET_PG_DSN", "postgresql://jafet:jafet@127.0.0.1:5433/jafet_books")


def conn():
    c = psycopg.connect(dsn())
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        c.execute(f.read())
    c.commit()
    return c


def vec_literal(vec):
    return "[" + ",".join(str(x) for x in vec) + "]"


def existing_mms_ids():
    c = conn()
    rows = c.execute("select mms_id from books").fetchall()
    c.close()
    return set(r[0] for r in rows)


def upsert_book(b):
    c = conn()
    row = c.execute(
        "insert into books(mms_id, title, author, isbn, publisher, place, edition, year,"
        "language, format, subjects, call_number, library, location, availability_status,"
        "description) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "on conflict (mms_id) do update set "
        "title = excluded.title, author = excluded.author, isbn = excluded.isbn,"
        "publisher = excluded.publisher, place = excluded.place, edition = excluded.edition,"
        "year = excluded.year, language = excluded.language, format = excluded.format,"
        "subjects = excluded.subjects, call_number = excluded.call_number,"
        "library = excluded.library, location = excluded.location,"
        "availability_status = excluded.availability_status,"
        # keep the old blurb when the new one is empty; embedding is never touched here
        "description = coalesce(nullif(excluded.description, ''), books.description) "
        "returning id",
        tuple(b.get(k) for k in COLS)).fetchone()
    c.commit()
    c.close()
    return row[0]


def set_embedding(mms_id, vec):
    c = conn()
    c.execute("update books set embedding = %s::vector where mms_id = %s",
              (vec_literal(vec), mms_id))
    c.commit()
    c.close()


def unembedded():
    c = conn()
    rows = c.execute("select mms_id, title, author, subjects, description from books "
                     "where embedding is null").fetchall()
    c.close()
    return [{"mms_id": r[0], "title": r[1], "author": r[2], "subjects": r[3],
             "description": r[4]} for r in rows]


def nearest_books(vec, k=5):
    c = conn()
    v = vec_literal(vec)
    rows = c.execute("select title, author, year, publisher, call_number, library, location,"
                     "availability_status, description, 1 - (embedding <=> %s::vector) "
                     "from books where embedding is not null "
                     "order by embedding <=> %s::vector limit %s", (v, v, k)).fetchall()
    c.close()
    return [{"title": r[0], "author": r[1], "year": r[2], "publisher": r[3],
             "call_number": r[4], "library": r[5], "location": r[6],
             "availability_status": r[7], "description": r[8],
             "similarity": float(r[9])} for r in rows]


def jsonable(v):
    # the tool result gets serialised to the model: timestamps, numerics and
    # vectors come back as objects json cannot encode
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    return str(v)


def run_select(sql, max_rows=50):
    """Runs agent-written SQL read only, so anything can come back including errors."""
    c = conn()
    try:
        cur = c.execute("set transaction read only")
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        if "embedding" in cols:
            # "select *" walks past the keyword guard and drags 3072 floats per row
            out = {"error": "the embedding column cannot be selected, "
                            "name the columns you need"}
        else:
            rows = [[jsonable(v) for v in r] for r in cur.fetchmany(max_rows)]
            out = {"columns": cols, "rows": rows}
    except psycopg.Error as e:
        out = {"error": str(e)}
    c.rollback()
    c.close()
    return out
