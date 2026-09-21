# DeskNotes backend — runs on Hugging Face Spaces (Docker) or any container host.
FROM python:3.12-slim

# HF Spaces runs the container as uid 1000; create that user and own the app dir.
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

COPY --chown=user . .

# media/uploads land here (writable by the app user; ephemeral on free tiers)
ENV DESKNOTES_DATA_DIR=/app/data
EXPOSE 7860

# HF Spaces expects port 7860; other hosts may inject $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
