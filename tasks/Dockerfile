# Task image: what each KubernetesPodOperator pod runs. Built as mwp-tasks:<version>.
#   docker build -t mwp-tasks:0.1.0 tasks/
FROM python:3.12.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MWP_DATA_DIR=/tmp/mwp-data

WORKDIR /app

# Dependencies first: this layer is cached and only rebuilt when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Never run as root inside a container.
RUN useradd --uid 1000 --no-create-home appuser
USER appuser
