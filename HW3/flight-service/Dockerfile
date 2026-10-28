FROM python:3.12-slim-bookworm AS proto
RUN pip install --no-cache-dir --disable-pip-version-check grpcio-tools==1.85.0
COPY proto /proto
RUN mkdir /gen && python -m grpc_tools.protoc -I /proto \
        --python_out=/gen --pyi_out=/gen --grpc_python_out=/gen /proto/flight/v1/flight.proto

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY flight-service/requirements.txt .
RUN pip install -r requirements.txt
COPY --from=proto /gen ./
COPY flight-service/app ./app
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 50051
CMD ["python", "-m", "app"]

FROM runtime AS test
RUN pip install --user pytest==9.1.1
COPY flight-service/tests ./tests
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
