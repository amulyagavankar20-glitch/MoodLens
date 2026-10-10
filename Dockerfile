FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# build with --build-arg DEEPFACE=1 to bake in the 7-mood engine
ARG DEEPFACE=0
COPY requirements-deepface.txt .
RUN if [ "$DEEPFACE" = "1" ]; then pip install --no-cache-dir -r requirements-deepface.txt; fi

COPY backend ./backend
COPY frontend ./frontend

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
