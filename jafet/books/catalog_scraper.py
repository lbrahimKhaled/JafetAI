import json
import re
import urllib.parse

import requests

BASE = "https://libcat.aub.edu.lb/primaws/rest/pub/pnxs"

# local records only, physically held (available_p) and books - without these
# facets the top hits are mostly central-index and e-only records
FACETS = urllib.parse.quote("facet_tlevel,exact,available_p|,|facet_rtype,exact,books")


def search_url(query, limit=30):
    return (BASE + "?acTriggered=false&blendFacetsSeparately=false&disableCache=false"
            "&getMore=0&inst=961AUB_INST&lang=en&limit=" + str(limit) +
            "&mode=advanced&offset=0&q=any,contains," + urllib.parse.quote(query) +
            "&qInclude=" + FACETS +
            "&scope=MyInstitution&tab=LibraryCatalog&vid=961AUB_INST:AUB_MAIN")


def fetch(url):
    # google.adk.tools.load_web_page runs the body through BeautifulSoup and then
    # drops every line of 4 words or fewer, which shreds this JSON (213KB -> 150KB,
    # json.loads fails) - so the endpoint is fetched raw instead
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def search_books_catalog(query, limit=30):
    return parse_records(fetch(search_url(query, limit)))


def _first(display, key):
    values = display.get(key)
    if not values:
        return ""
    return values[0]


def _author(display):
    # "Winston, Patrick Henry.$$QWinston, Patrick Henry." -> the part before $$Q
    return _first(display, "creator").split("$$Q")[0].strip()


def _isbn(doc, display):
    isbns = doc["pnx"].get("addata", {}).get("isbn")
    if isbns:
        return isbns[0]
    # local records bury it in "$$CISBN$$V0201533774", PC records use "ISBN: 1041228376"
    m = re.search(r"ISBN\D{0,4}([\dXx-]{10,17})", " ".join(display.get("identifier", [])))
    if m:
        return m.group(1)
    return ""


def _year(display):
    m = re.search(r"\d{4}", _first(display, "creationdate"))
    if m:
        return int(m.group())
    return None


def _mms_id(doc, display):
    mms = _first(display, "mms")
    if mms:
        return mms
    # recordid is the same id behind a source prefix, e.g. "alma9910080373597..."
    recordid = _first(doc["pnx"].get("control", {}), "recordid")
    return recordid.replace("alma", "")


def _parse_doc(doc):
    if doc.get("context") != "L":
        return None
    display = doc["pnx"]["display"]
    if display.get("type") != ["book"]:
        return None
    best = (doc.get("delivery") or {}).get("bestlocation")
    if not best:
        return None

    title = _first(display, "title").strip()
    mms_id = _mms_id(doc, display)
    if not title or not mms_id:
        return None

    return {
        "mms_id": mms_id,
        "title": title,
        "author": _author(display),
        "isbn": _isbn(doc, display),
        "publisher": _first(display, "publisher"),
        "place": _first(display, "place"),
        "edition": _first(display, "edition"),
        "year": _year(display),
        "language": _first(display, "language"),
        "format": _first(display, "format"),
        "subjects": display.get("subject", []),
        "call_number": best.get("callNumber", ""),
        "library": best.get("mainLocation", ""),
        "location": best.get("subLocation", ""),
        "availability_status": best.get("availabilityStatus", ""),
        "description": " ".join(display.get("description", [])),
    }


def parse_records(raw):
    if isinstance(raw, str):
        raw = json.loads(raw)
    records = []
    for doc in raw.get("docs", []):
        rec = _parse_doc(doc)
        if rec is not None:
            records.append(rec)
    return records
