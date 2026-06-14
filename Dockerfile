FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cart_optimizer/ ./cart_optimizer/
COPY webapp/ ./webapp/

# Shared coupon DB lives on a persistent disk mounted at /data (see render.yaml).
ENV COUPON_DB=/data/coupons.db
EXPOSE 8000

# Render/most PaaS inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
