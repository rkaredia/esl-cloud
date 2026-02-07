FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 1. Install system dependencies
# gcc and musl-dev are often needed for compiling Python packages
# libpq-dev is needed for Postgres (psycopg2)
# libjpeg-dev and zlib1g-dev are needed for Image processing (Pillow)
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# 2. Create the user and handle permissions
RUN useradd -ms /bin/bash django && \
    chown -R django:django /app

# 3. Create media and static folders if they don't exist and give 'django' permission
RUN mkdir -p /app/media /app/static && \
    chown -R django:django /app/media /app/static

USER django