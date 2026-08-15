# Minimal image for the key-free demo: no model, no API key, no network.
#   docker build -t groundextract-kr .
#   docker run --rm groundextract-kr                                # demo
#   docker run --rm groundextract-kr python -m groundextract.bench  # NumHall-KR
FROM python:3.11-slim
WORKDIR /app
COPY . .
# The entire runtime dependency surface is one package.
RUN pip install --no-cache-dir "pyyaml>=6.0"
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
CMD ["python", "-m", "groundextract"]
