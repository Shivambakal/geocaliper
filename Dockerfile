FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY samples ./samples

EXPOSE 8000

# single worker is fine for an assignment; bump for real traffic
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
