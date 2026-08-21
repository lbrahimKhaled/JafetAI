from jafet.books import rag

LONG_BLURB = ("word " * 200).strip()

BOOK = {"title": "Deep Learning", "author": "Goodfellow, Ian", "year": 2016,
        "publisher": "MIT Press", "call_number": "Q325.5 .G66 2016",
        "library": "Jafet", "location": "Stacks", "availability_status": "Available",
        "description": LONG_BLURB, "similarity": 0.8123456}


def fake_rows(rows):
    def nearest(vec, k=5):
        return rows[:k]
    return nearest


def stub(monkeypatch, rows, vec=None):
    monkeypatch.setattr(rag, "embed_query", lambda q: vec or [0.1, 0.2, 0.3])
    monkeypatch.setattr(rag, "nearest_books", fake_rows(rows))


def test_happy_path(monkeypatch):
    stub(monkeypatch, [BOOK])
    out = rag.search_books("  neural networks textbook  ")
    assert out["query"] == "neural networks textbook"
    assert "note" not in out and "error" not in out
    m = out["matches"][0]
    assert m["title"] == "Deep Learning"
    assert m["call_number"] == "Q325.5 .G66 2016"
    assert m["availability_status"] == "Available"
    assert m["similarity"] == 0.812
    assert "publisher" not in m


def test_description_truncated_at_word_boundary(monkeypatch):
    stub(monkeypatch, [BOOK])
    d = rag.search_books("neural networks")["matches"][0]["description"]
    assert d.endswith("...")
    assert len(d) <= rag.BLURB + 3
    assert d[:-3] in LONG_BLURB  # cut on a space, no half word left behind


def test_short_description_untouched(monkeypatch):
    book = dict(BOOK, description="A short blurb.")
    stub(monkeypatch, [book])
    assert rag.search_books("x")["matches"][0]["description"] == "A short blurb."


def test_empty_query_rejected(monkeypatch):
    stub(monkeypatch, [BOOK])
    assert "error" in rag.search_books("   ")


def test_long_query_rejected(monkeypatch):
    stub(monkeypatch, [BOOK])
    assert "error" in rag.search_books("a" * (rag.MAX_QUERY + 1))


def test_no_books_note(monkeypatch):
    stub(monkeypatch, [])
    out = rag.search_books("anything")
    assert out["matches"] == []
    assert out["note"] == "no books in the database yet"


def test_weak_matches_note(monkeypatch):
    stub(monkeypatch, [dict(BOOK, similarity=0.2)])
    out = rag.search_books("cooking with quantum fields")
    assert "weak matches" in out["note"]
    assert out["matches"][0]["similarity"] == 0.2


def test_embedding_failure_returns_error(monkeypatch):
    def boom(q):
        raise RuntimeError("openai down")
    monkeypatch.setattr(rag, "embed_query", boom)
    monkeypatch.setattr(rag, "nearest_books", fake_rows([BOOK]))
    out = rag.search_books("neural networks")
    assert out["error"] == "embedding service failed: openai down"
