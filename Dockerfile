FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway Volume 持久化目录
RUN mkdir -p /data
ENV DATA_DIR=/data

EXPOSE 8800

CMD ["python", "main.py"]
