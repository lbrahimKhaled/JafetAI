import os

from openai import OpenAI

MODEL = "text-embedding-3-large"


def embed_texts(texts):
    client = OpenAI(api_key=os.environ["OPEN_AI_KEY"])
    r = client.embeddings.create(model=MODEL, input=texts)
    return [d.embedding for d in r.data]


def embed_query(text):
    return embed_texts([text])[0]
