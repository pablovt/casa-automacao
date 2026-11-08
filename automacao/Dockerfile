FROM python:3.12-alpine

WORKDIR /app

# Sem dependências externas — só stdlib.
COPY main.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "main.py"]
