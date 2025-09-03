FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install -U pip && pip install .
EXPOSE 8000
CMD ["maxsat-runner", "serve", "--host", "0.0.0.0", "--port", "8000"]
