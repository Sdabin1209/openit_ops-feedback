# ingest-api 이미지. 설정은 환경 변수로만 받고 비밀 값은 넣지 않는다. 로그는 표준 출력. 비root로 실행.
FROM python:3.12-slim AS build
WORKDIR /src
COPY build/wheels /wheels
COPY pyproject.toml requirements.lock /src/
COPY src /src/src
COPY schemas /src/schemas
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock \
 && pip install --no-cache-dir --prefix=/install --no-deps /wheels/*.whl /src

FROM python:3.12-slim
# 값이 없다는 표기는 n/a 하나다(계약 ImageLabels). COMMIT·CONTRACTS_VERSION·SURVEY_SCHEMA_VERSION은 빌드가 실제 값을 넘기며, 안 넘기면 게시 검사가 거부한다.
ARG COMMIT=n/a
ARG CONTRACTS_VERSION=n/a
ARG SURVEY_SCHEMA_VERSION=n/a
ARG MODEL_RUNTIME_VERSION=n/a
LABEL io.openit.service="ops-feedback" \
      io.openit.revision=$COMMIT \
      io.openit.contracts.boundary=$CONTRACTS_VERSION \
      io.openit.contracts.survey-schema=$SURVEY_SCHEMA_VERSION \
      io.openit.model-runtime=$MODEL_RUNTIME_VERSION
COPY --from=build /install /usr/local
RUN useradd --system --uid 10001 --no-create-home app && mkdir /data && chown app /data
USER app
ENV PYTHONUNBUFFERED=1 OPS_LOG_DB=/data/log.db OPS_FAILURE_DB=/data/failures.db OPS_PORT=8080
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('OPS_PORT','8080')+'/healthz')"]
CMD ["python", "-m", "ops_feedback.api"]
