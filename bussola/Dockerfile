FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY app.py /app/app.py
RUN pip install --no-cache-dir psycopg2-binary flask requests
EXPOSE 8000
CMD ["python","app.py"]
