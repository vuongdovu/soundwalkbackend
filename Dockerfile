# =============================================================================
# Multi-Stage Dockerfile for Django 5.2 LTS Application (Python 3.14)
# =============================================================================
# Stage 1 (builder): Install dependencies and compile any C extensions
# Stage 2 (production): Lean runtime image without build tools
#
# Build: docker build -t myapp .
# Run: docker run -p 8080:8080 myapp
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder
# -----------------------------------------------------------------------------
# This stage installs all Python dependencies, including those with C extensions.
# Build tools and headers are included but won't be in the final image.
FROM python:3.14-slim AS builder

# Prevent Python from writing bytecode and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # pip configuration
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies required for compiling Python packages
# - gcc: C compiler for packages with C extensions
# - libpq-dev: PostgreSQL client library headers (for psycopg)
# - python3-dev: Python headers for C extensions
# - libjpeg-dev, zlib1g-dev: Image processing headers for Pillow
# - libwebp-dev: WebP image format headers for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
# Using venv ensures clean separation and easy copying to production stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt


# -----------------------------------------------------------------------------
# Stage 2: Production Runtime
# -----------------------------------------------------------------------------
# Minimal image with only runtime dependencies. No build tools, smaller attack surface.
FROM python:3.14-slim AS production

# Prevent Python from writing bytecode and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Add virtual environment to PATH
    PATH="/opt/venv/bin:$PATH" \
    # Django settings
    DJANGO_SETTINGS_MODULE=config.settings

# Install only runtime dependencies (no build tools)
# - libpq5: PostgreSQL client library (runtime only, not -dev headers)
# - curl: For health checks
# - libjpeg62-turbo, zlib1g: Image processing runtime libraries for Pillow
# - libwebp7, libwebpmux3, libwebpdemux2: WebP image format runtime libraries
# - libmagic1: Magic number detection for python-magic (MIME type detection)
# - ffmpeg: Video processing (poster frame extraction, metadata)
# - poppler-utils: PDF processing (thumbnail generation via pdf2image)
# - libreoffice-writer-nogui: Office document conversion (Word to PDF)
# - libreoffice-calc-nogui: Office document conversion (Excel to PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libjpeg62-turbo \
    zlib1g \
    libwebp7 \
    libwebpmux3 \
    libwebpdemux2 \
    libmagic1 \
    ffmpeg \
    poppler-utils \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
# Running as root in containers is a security risk
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY --chown=appuser:appgroup app/ /app/

# Copy entrypoint script
COPY --chown=appuser:appgroup scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create directories for static, media, and log files
RUN mkdir -p /app/staticfiles /app/media /app/logs && \
    chown -R appuser:appgroup /app/staticfiles /app/media /app/logs

# Switch to non-root user
USER appuser

# Expose port (Uvicorn will listen on this)
EXPOSE 8080

# Health check - ensures container is serving requests
# Django health endpoint should return 200 OK
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health/ || exit 1

# Default command: run entrypoint script which starts Uvicorn
ENTRYPOINT ["/entrypoint.sh"]

# Default arguments to entrypoint (can be overridden)
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8080"]
