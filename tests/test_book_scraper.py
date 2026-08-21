import json
import os

from jafet.books import catalog_scraper

# a trimmed real Primo response: 2 local books with holdings, then a PC record,
# a journal, an e-book with no holding, and two records missing title / mms id
FIXTURE = os.path.join(os.path.dirname(__file__), "primo_search.json")
RAW = open(FIXTURE).read()
DOCS = json.loads(RAW)["docs"]


def test_parses_a_local_book_end_to_end():
    rec = catalog_scraper.parse_records({"docs": [DOCS[0]]})[0]
    assert rec == {
        "mms_id": "991008037359709436",
        "title": "Artificial intelligence",
        "author": "Winston, Patrick Henry.",
        "isbn": "0201533774",
        "publisher": "Reading MA : Addison-Wesley",
        "place": "Reading MA :",
        "edition": "3rd ed.",
        "year": 1992,
        "language": "eng",
        "format": "xxv, 737 p. : ill. ; 25 cm.",
        "subjects": ["Artificial intelligence"],
        "call_number": "J 001.535:W783a3:c.1",
        "library": "Jafet Library",
        "location": "Jafet:J",
        "availability_status": "available",
        "description": "",
    }


def test_creator_markup_stripped():
    rec = catalog_scraper.parse_records({"docs": [DOCS[1]]})[0]
    assert rec["author"] == "McNeill, William Hardy, 1917-2016. author."


def test_year_from_c1992():
    assert catalog_scraper.parse_records({"docs": [DOCS[0]]})[0]["year"] == 1992
    assert catalog_scraper.parse_records({"docs": [DOCS[1]]})[0]["year"] == 1979


def test_isbn_falls_back_to_identifier_markup():
    doc = json.loads(json.dumps(DOCS[0]))
    doc["pnx"]["addata"] = {}
    assert catalog_scraper.parse_records({"docs": [doc]})[0]["isbn"] == "0201533774"


def test_description_paragraphs_joined():
    rec = catalog_scraper.parse_records({"docs": [DOCS[1]]})[0]
    assert rec["description"].startswith("Global in scope")
    assert "William Hardy McNeill was born" in rec["description"]
    assert "discoveries. William" in rec["description"]


def test_online_journal_and_unheld_records_filtered():
    # PC (central index), a journal, and a book whose bestlocation is null
    assert catalog_scraper.parse_records({"docs": DOCS[2:5]}) == []


def test_records_without_title_or_mms_id_skipped():
    assert catalog_scraper.parse_records({"docs": DOCS[5:7]}) == []


def test_parses_raw_json_text():
    records = catalog_scraper.parse_records(RAW)
    assert [r["mms_id"] for r in records] == ["991008037359709436", "991007133899709436"]


def test_search_builds_url_and_parses(monkeypatch):
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return RAW

    monkeypatch.setattr(catalog_scraper, "fetch", fake_fetch)
    records = catalog_scraper.search_books_catalog("arabic literature", limit=10)
    assert "q=any,contains,arabic%20literature" in seen["url"]
    assert "limit=10" in seen["url"]
    assert len(records) == 2
