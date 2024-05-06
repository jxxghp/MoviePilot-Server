FROM ubuntu:latest
ENV LANG="C.UTF-8" \
    TZ="Asia/Shanghai" \
    REPO_URL="https://github.com/jxxghp/MoviePilot-Server.git" \
    CONFIG_DIR="/config" \
    WORKDIR="/app"
RUN apt-get update && apt-get install -y git python3 pipx
RUN git clone -b main ${REPO_URL} ${WORKDIR}
WORKDIR ${WORKDIR}
RUN pipx install -r requirements.txt
VOLUME ["/config"]
EXPOSE 3001
CMD ["uvicorn", "main:App", "--host", "0.0.0.0", "--port", "3001"]
