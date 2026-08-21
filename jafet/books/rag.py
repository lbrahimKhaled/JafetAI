from .bookdb import nearest_books
from .embeddings import embed_query

MAX_QUERY = 500
BLURB = 300
WEAK = 0.22  # calibrated on the real corpus: on-topic queries score ~0.28-0.43,
             # nonsense ~0.16 - below this the neighbours are not about the topic


def shorten(text):
    if not text or len(text) <= BLURB:
        return text or ""
    return text[:BLURB].rsplit(" ", 1)[0] + "..."


def search_books(query: str) -> dict:
    """Semantic search over the AUB library catalog database.
    Pass a rewritten, enriched topic query (subject wording, synonyms, field terms),
    not the student's raw words. Returns the 5 closest books with call number,
    library, location and availability."""
    query = query.strip()
    if not query:
        return {"error": "query is empty"}
    if len(query) > MAX_QUERY:
        return {"error": "query is too long, keep it under "
                + str(MAX_QUERY) + " characters"}
    try:
        vec = embed_query(query)
    except Exception as e:
        return {"error": "embedding service failed: " + str(e)}

    matches = []
    for r in nearest_books(vec, 5):
        matches.append({
            "title": r["title"],
            "author": r["author"],
            "year": r["year"],
            "call_number": r["call_number"],
            "library": r["library"],
            "location": r["location"],
            "availability_status": r["availability_status"],
            "similarity": round(r["similarity"], 3),
            "description": shorten(r["description"]),
        })

    out = {"query": query, "matches": matches}
    if not matches:
        out["note"] = "no books in the database yet"
    elif matches[0]["similarity"] < WEAK:
        out["note"] = ("weak matches - consider rewriting the query or telling the "
                       "user nothing close was found")
    return out
