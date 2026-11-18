FROM python:3.12-slim

WORKDIR /app/webui
ENV PYTHONPATH=/app/webui
COPY webui/requirements.txt /app/webui/requirements.txt
COPY worker/requirements.txt /app/worker/requirements.txt
RUN pip install --no-cache-dir -r /app/worker/requirements.txt
# Reuse the paper-processing handlers until they become a shared package.
COPY webui/app.py webui/ocr.py webui/markdown_service.py webui/metadata_service.py webui/reference_service.py webui/task_queue.py ./
COPY webui/static ./static
COPY worker/worker.py /app/worker/worker.py

CMD ["python", "/app/worker/worker.py"]
