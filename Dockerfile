# AI-Gate: ядро на чистой stdlib Python — без pip-зависимостей,
# образ собирается и в air-gapped средах.
FROM python:3.12-slim

WORKDIR /app
COPY gateway/ gateway/

# непривилегированный пользователь; данные (аудит/политика) — в volume
RUN useradd -m aigate && mkdir -p /app/data && chown -R aigate:aigate /app
USER aigate

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"

CMD ["python", "-m", "gateway.main"]
