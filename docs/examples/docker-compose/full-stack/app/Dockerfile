FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask>=3.0 google-cloud-bigquery>=3.20
COPY server.py ./
EXPOSE 8080
CMD ["python", "server.py"]
