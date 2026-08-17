# BFF (Backend-For-Frontend) in Python con httpx
# Questo è il "weak link": un reverse proxy scritto a mano con httpx
# che forwarda risposte raw dal back-end senza difesa.
#
# Pattern realistico: FastAPI/Starlette app che fa da API gateway.
# In the wild si trova in startup che hanno microservizi dietro un gateway Python.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="crlf-bff"
LABEL org.opencontainers.image.description="BFF Python con httpx — weak link che forwarda stacked responses"

WORKDIR /app

RUN pip install --no-cache-dir "httpx[http2]" uvicorn

# L'app: un reverse proxy che forwarda tutto al back-end usando httpx
COPY app.py /app/app.py

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]