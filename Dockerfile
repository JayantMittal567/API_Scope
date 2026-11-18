# ApiScope 1.0 — Production-Ready Container Image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APISCOPE_HOST=0.0.0.0 \
    APISCOPE_PORT=8000

WORKDIR /app

# Create dedicated non-root user and group
RUN groupadd -g 10001 apiscope && \
    useradd -u 10001 -g apiscope -s /bin/bash -m apiscope

# Copy project definition and readme first for cached builds
COPY --chown=apiscope:apiscope pyproject.toml README.md /app/

# Copy application source code
COPY --chown=apiscope:apiscope src/ /app/src/

# Install ApiScope in production mode
RUN pip install --no-cache-dir .

# Create directory for persistent runtime data and history database
RUN mkdir -p /app/.apiscope /app/data && \
    chown -R apiscope:apiscope /app/.apiscope /app/data

# Switch to non-root user
USER apiscope

# Expose default dashboard web interface port
EXPOSE 8000

# Default command launches the read-only dashboard server
# No automatic API tests, Gemini calls, or target mutations are executed
ENTRYPOINT ["apiscope"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--db-path", "/app/.apiscope/history.db"]
