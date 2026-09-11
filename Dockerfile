FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-audio.txt ./
RUN pip install --no-cache-dir -r requirements-audio.txt

COPY bot_tele_parrot.py integrations.py extra_commands.py ./

RUN useradd --system --create-home bot \
    && mkdir -p /data \
    && chown bot /data
USER bot

CMD ["python", "bot_tele_parrot.py"]
