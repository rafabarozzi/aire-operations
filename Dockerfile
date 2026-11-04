FROM python:3.12-slim
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY operations operations
COPY contracts contracts
COPY migrations migrations
COPY tests tests
RUN useradd --create-home aire
USER aire
CMD ["python","-m","uvicorn","operations.api:app","--host","0.0.0.0","--port","8080"]
