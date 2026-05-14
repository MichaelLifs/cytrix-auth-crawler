# Keep Playwright image version aligned with Python package to avoid browser mismatch.
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/sessions

CMD ["python", "main.py"]
