# syntax=docker/dockerfile:1.7.0

# ARG PYTHON_VERSION=3.12
# FROM python:${PYTHON_VERSION}-slim AS base
FROM python@sha256:f1b1cd069b00fe52d0c4177098d7825e70b83f2c70b5c9d7e9e68789b3a5a5d9

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install -r requirements.txt

USER appuser
COPY . .
EXPOSE 8000

CMD ["python", "app.py"]