FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS admin-builder
WORKDIR /build/admin-ui
COPY admin-ui/package.json admin-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY admin-ui/ ./
RUN npm run build

FROM python:3.11.9-bullseye
ENV LANG="C.UTF-8" \
    TZ="Asia/Shanghai" \
    REPO_URL="https://github.com/jxxghp/MoviePilot-Server.git" \
    CONFIG_DIR="/config" \
    WORKDIR="/app"
WORKDIR ${WORKDIR}
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY main.py ./
COPY --from=admin-builder /build/admin-ui/dist ./admin-ui/dist
VOLUME ["/config"]
EXPOSE 3001
CMD ["python3", "main.py"]
