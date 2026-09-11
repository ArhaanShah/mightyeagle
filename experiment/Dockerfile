FROM python:3.12.10-slim-bookworm

COPY container-requirements.txt /opt/container-requirements.txt
RUN pip install --no-cache-dir --requirement /opt/container-requirements.txt \
    && useradd --no-create-home --shell /usr/sbin/nologin validator

USER validator
WORKDIR /task
