FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV JUSTBUILD_DATA_DIR=/data/jobs
VOLUME ["/data"]
EXPOSE 8765
CMD ["justbuild", "serve", "--host", "0.0.0.0", "--port", "8765", "--data-dir", "/data/jobs"]

