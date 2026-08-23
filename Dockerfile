FROM python:3.12-slim

WORKDIR /app

COPY email_pipeline/requirements.txt email_pipeline/requirements.txt
RUN pip install --no-cache-dir -r email_pipeline/requirements.txt
# installs Chromium + the OS libraries it needs, for the form-outreach crawler
RUN playwright install --with-deps chromium

COPY email_pipeline email_pipeline

# overridden by DB_PATH env var in compose to point at a mounted volume
# so the contacts/settings DB survives container rebuilds
ENV DB_PATH=/data/pipeline.db

EXPOSE 8000

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "email_pipeline.wsgi:app"]
