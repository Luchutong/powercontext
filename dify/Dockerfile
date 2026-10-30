FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /opt/life
COPY requirements-linux-py312.lock .
RUN pip install --no-cache-dir -r requirements-linux-py312.lock \
    && useradd --uid 10001 --create-home life \
    && mkdir /data && chown life:life /data
COPY app app
COPY story story
COPY scripts scripts
USER life
EXPOSE 18381
CMD ["python", "scripts/run_container.py"]
