FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY app ./app
COPY sql ./sql
RUN pip install --no-cache-dir .

ENTRYPOINT ["hithink-snapshot"]
CMD ["run"]
