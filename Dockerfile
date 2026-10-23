FROM python@sha256:9d3abd9fc11d06998ccdbdd93b4dd49b5ad7d67fcbbc11c016eb0eb2c2194891
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/app/data TZ=Asia/Shanghai PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium && apt-get install -y --no-install-recommends libgl1 && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /opt/playwright
RUN useradd --uid 10001 --create-home radar
COPY app ./app
COPY seed ./seed
COPY tests ./tests
RUN mkdir /app/data && chown radar:radar /app/data
USER radar
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log"]
