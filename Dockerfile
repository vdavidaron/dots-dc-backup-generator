FROM python:3.13

RUN mkdir /app/
WORKDIR /app

COPY src/BackupgenService src/BackupgenService
COPY pyproject.toml ./
COPY README.md ./
RUN pip install ./

ENTRYPOINT python3 src/BackupgenService/backupgenservice.py