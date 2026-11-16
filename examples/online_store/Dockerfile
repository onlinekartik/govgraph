# Build from the repository root:  docker build -f examples/online_store/Dockerfile .
FROM python:3.12-slim

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY examples/online_store/run_demo.py /app/run_demo.py
RUN useradd --create-home demo
USER demo
WORKDIR /app
ENV PYTHONUNBUFFERED=1
CMD ["python", "run_demo.py"]
