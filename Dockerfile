FROM python:3.12-slim

# fonte com acentos para o PDF (DejaVu)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn psycopg2-binary

COPY app.py db.py importadores.py pdf_pedido.py ia_protocolo.py ./
COPY templates ./templates
COPY static ./static

ENV PORT=8080
EXPOSE 8080
CMD gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 app:app
