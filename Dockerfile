FROM python:3.12-slim

# Install FFmpeg, curl (healthcheck), and create non-root user in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 -s /usr/sbin/nologin appuser

WORKDIR /app

# Copy dependency manifest and app package for better layer caching
COPY pyproject.toml .
COPY app ./app

# Install runtime dependencies and yt-dlp (for YouTube server-side fallback when stream proxy fails)
RUN pip install --no-cache-dir . yt-dlp

# Copy rest of app context (cookies dir, etc.)
COPY . .

# Ensure cookies dir exists and is owned by appuser
RUN mkdir -p /app/cookies && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
