FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    USE_LOCAL_MEDIA=False

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Static files use local storage at build time; runtime media uses R2.
RUN USE_LOCAL_MEDIA=True python manage.py collectstatic --noinput

# Railway reads EXPOSE to work out which port to route the public domain to.
# Without it a Dockerfile service gets "Application failed to respond" even
# though gunicorn is up, because the edge is talking to the wrong port.
EXPOSE 8080

# Shell form so ${PORT} expands; exec so gunicorn becomes PID 1 and receives
# Railway's SIGTERM directly instead of it stopping at /bin/sh.
# Workers x threads = concurrent requests. Most API requests are cache hits, so
# threads are cheap; WEB_CONCURRENCY / GUNICORN_THREADS tune it per plan size.
# The long timeout is for admin uploads, where videos are processed in-request.
CMD python manage.py migrate --noinput && \
    exec gunicorn config.wsgi:application \
        --bind 0.0.0.0:${PORT:-8080} \
        --workers ${WEB_CONCURRENCY:-2} \
        --threads ${GUNICORN_THREADS:-4} \
        --timeout 180 \
        --max-requests 2000 \
        --max-requests-jitter 200 \
        --access-logfile - \
        --error-logfile -
