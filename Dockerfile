FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY . /app

ENTRYPOINT ["python3"]
CMD ["watcher.py"]
