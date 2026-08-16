# Minimal image for the key-free demo: no model, no API key, no network.
#   docker build -t groundextract-kr .
#   docker run --rm groundextract-kr                                # demo
#   docker run --rm groundextract-kr python -m groundextract.bench  # NumHall-KR
FROM python:3.11-slim
WORKDIR /app
# Allowlist, not `COPY . .`. A denylist in .dockerignore has to be updated every
# time a new private file appears next to the source, and the one time it is
# forgotten the file is baked into a layer that survives being deleted later.
# Naming what ships means an unlisted file cannot leak by default — which matters
# here because the working tree legitimately holds unmasked source documents and
# local notes that .gitignore keeps out of the repository.
COPY pyproject.toml README.md README.ko.md LICENSE ./
COPY groundextract/ ./groundextract/
COPY rules/ ./rules/
COPY bench/golden/ ./bench/golden/
# The entire runtime dependency surface is one package.
RUN pip install --no-cache-dir "pyyaml>=6.0"
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
# Nothing here needs to write to the image or bind a port, so drop root.
USER nobody
CMD ["python", "-m", "groundextract"]
