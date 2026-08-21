FROM python:3.13-slim

# psycopg[binary] ships its own libpq, so there is nothing to build here; curl is for the
# compose healthcheck, which is what tells ClassMate's side the chat endpoint is up
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jafet/ ./jafet/
COPY http_server.py mcp_server.py ./

# the sqlite seat cache and booking history live on a volume, not in the image layer
ENV JAFET_DB=/data/jafet.db
ENV PYTHONUNBUFFERED=1

# 0.0.0.0 inside the container is not the same decision as 0.0.0.0 on the host: what the
# endpoint is reachable from is set by the compose network and the published port, and both
# keep it off the public interface
ENV JAFET_HTTP_HOST=0.0.0.0
ENV JAFET_HTTP_PORT=8802

EXPOSE 8802

CMD ["python", "http_server.py"]
