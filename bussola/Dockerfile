FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY app.py /app/app.py
COPY wsgi.py /app/wsgi.py
RUN pip install --no-cache-dir psycopg2-binary flask requests gunicorn
EXPOSE 8000
# Um unico processo garante que exista apenas um agendador. As threads deixam o
# healthcheck responder mesmo enquanto uma sincronizacao longa esta em curso.
CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "180", "-b", "0.0.0.0:8000", "wsgi:app"]
