FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Tehran
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt
COPY agent.py sources.json channels.txt ./
RUN mkdir -p /app/data
CMD ["python", "-u", "agent.py"]
