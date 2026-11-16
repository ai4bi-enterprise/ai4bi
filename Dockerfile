FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /code

COPY ./requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY . /code

# Chạy bằng user không phải root (tương thích Hugging Face Spaces). DB mẫu mở ở chế độ chỉ đọc.
RUN useradd -m -u 1000 appuser && chown -R appuser /code
USER appuser

# Cổng 7860 để tương thích Hugging Face Spaces (Docker SDK)
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
