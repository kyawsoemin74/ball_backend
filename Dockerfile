FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV POETRY_VIRTUALENVS_CREATE=false

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN addgroup --system --gid 1000 app && adduser --system --uid 1000 --ingroup app app \
	&& mkdir -p /var/www/fover/uploads/news \
	&& chown -R app:app /usr/src/app /var/www/fover
USER app

EXPOSE 8000 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
