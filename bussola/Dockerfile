FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && groupadd --gid 10001 pedemeia \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin pedemeia
COPY app.py /app/app.py
COPY wsgi.py /app/wsgi.py
USER 10001:10001
EXPOSE 8000
# Um unico processo garante que exista apenas um agendador. As threads deixam o
# healthcheck responder mesmo enquanto uma sincronizacao longa esta em curso.
# A imagem roda sem privilegios e /app nao e gravavel. O Gunicorn 26 tenta
# criar ali um socket de controle por padrao e gera "Permission denied"; o app
# nao usa essa interface, entao a desativamos explicitamente.
CMD ["gunicorn", "--no-control-socket", "-w", "1", "--threads", "4", "--timeout", "180", "-b", "0.0.0.0:8000", "wsgi:app"]
