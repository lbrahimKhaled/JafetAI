import os

from dotenv import load_dotenv
from litellm import completion

from jafet.books import bookdb
from jafet.books.catalog_scraper import search_books_catalog
from jafet.books.embeddings import embed_texts

CATEGORIES = ["artificial intelligence", "world history", "philosophy", "economics",
              "psychology", "physics", "medicine", "arabic literature", "engineering",
              "political science"]
PER_CATEGORY = 15
# small headroom over PER_CATEGORY for dedup and the odd unparsable record
SEARCH_LIMIT = 30
DESCRIBE_MODEL = "nvidia_nim/nvidia/nemotron-3-super-120b-a12b"

# nemotron likes to think out loud, so the format is spelled out
INSTRUCTION = ("Write one factual paragraph of 80 to 120 words describing this library "
               "book: what it covers and who it is for. Reply with the paragraph only - "
               "no preamble, no reasoning, no headings.\n\n")


def describe(rec):
    facts = ("Title: " + rec["title"] +
             "\nAuthor: " + rec["author"] +
             "\nYear: " + str(rec["year"] or "") +
             "\nSubjects: " + ", ".join(rec["subjects"]) +
             "\nPublisher: " + rec["publisher"])
    r = completion(model=DESCRIBE_MODEL,
                   messages=[{"role": "user", "content": INSTRUCTION + facts}])
    return r.choices[0].message.content.strip()


def main():
    load_dotenv()
    # litellm wants NVIDIA_NIM_API_KEY, .env has NVIDIA_API_KEY
    if "NVIDIA_NIM_API_KEY" not in os.environ:
        os.environ["NVIDIA_NIM_API_KEY"] = os.environ.get("NVIDIA_API_KEY", "")

    seen = bookdb.existing_mms_ids()
    fetched = 0
    new = []
    for query in CATEGORIES:
        records = search_books_catalog(query, SEARCH_LIMIT)
        fetched += len(records)
        picked = 0
        for rec in records:
            if picked == PER_CATEGORY:
                break
            if rec["mms_id"] in seen:
                continue
            seen.add(rec["mms_id"])
            new.append(rec)
            picked += 1
        print(query + ": " + str(len(records)) + " books, " + str(picked) + " new")

    print("books fetched: " + str(fetched))
    print("new: " + str(len(new)))

    described = 0
    for rec in new:
        if not rec["description"]:
            rec["description"] = describe(rec)
            described += 1
    print("described: " + str(described))

    for rec in new:
        bookdb.upsert_book(rec)

    rows = bookdb.unembedded()
    if rows:
        texts = [r["title"] + " by " + r["author"] + ". Subjects: " +
                 ", ".join(r["subjects"]) + ". " + r["description"] for r in rows]
        vectors = embed_texts(texts)
        for row, vector in zip(rows, vectors):
            bookdb.set_embedding(row["mms_id"], vector)
    print("embedded: " + str(len(rows)))


if __name__ == "__main__":
    main()
