FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /code

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY . /code

# Chạy bằng user không phải root (tương thích Hugging Face Spaces / Render). CSDL mẫu chỉ đọc.
RUN useradd -m -u 1000 appuser && chown -R appuser /code
USER appuser

# Mặc định cổng 7860 (Hugging Face Spaces); Render/Fly đặt biến PORT
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
