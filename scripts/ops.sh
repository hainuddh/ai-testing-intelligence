#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${ATI_PROJECT_DIR:-/home/admin/ai-testing-intelligence}"
PROXY_SERVICE="${ATI_PROXY_SERVICE:-ai-testing-intelligence-proxy.service}"
DOMAIN="${ATI_DOMAIN:-api.ddhlf.xyz}"
LOCAL_HEALTH_URL="${ATI_LOCAL_HEALTH_URL:-http://127.0.0.1:8080/api/v1/health}"
PUBLIC_HEALTH_URL="${ATI_PUBLIC_HEALTH_URL:-https://${DOMAIN}/api/v1/health}"
BACKUP_DIR="${ATI_BACKUP_DIR:-${PROJECT_DIR}/backups}"
HEALTH_RETRIES="${ATI_HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${ATI_HEALTH_INTERVAL:-2}"
MIN_FREE_MB="${ATI_MIN_FREE_MB:-2048}"

COMPOSE=(docker compose)

usage() {
    cat <<'EOF'
AI Testing Intelligence 运维脚本

用法：
  ./scripts/ops.sh <命令> [参数]

应用与代理：
  start                 启动 Compose 应用，健康后启动 HTTPS 代理
  stop                  先停止 HTTPS 代理，再停止 Compose 应用
  restart               重启应用和代理，并执行健康检查
  app-start             只启动 Compose 应用
  app-stop              只停止 Compose 应用（保留容器和数据卷）
  app-restart           只重启 Compose 应用
  proxy-start           启动 HTTPS 代理
  proxy-stop            停止 HTTPS 代理
  proxy-restart         重启 HTTPS 代理

状态与排障：
  status                查看 Compose、代理、端口、磁盘和健康状态
  health                检查本地及 HTTPS 健康接口
  logs [服务]           持续查看日志；可指定 api/web/postgres/redis/minio/proxy
  logs-tail [服务]      查看最近 200 行日志
  db-shell              进入 PostgreSQL psql
  db-counts             查看主要业务表的数据量

升级与备份：
  build                 拉取基础镜像并重新构建应用镜像
  deploy                用当前代码构建、部署并验证（不执行 Git 更新）
  upgrade [分支或标签]  备份数据库、更新 Git、构建、部署并验证
  backup [名称]         创建 PostgreSQL 自定义格式备份
  config-check          校验 Compose、Python 和 systemd 配置

环境变量：
  ATI_PROJECT_DIR       项目目录
  ATI_DOMAIN            HTTPS 域名
  ATI_BACKUP_DIR        备份目录
  ATI_MIN_FREE_MB       升级所需最小磁盘空间，默认 2048 MB

安全说明：
  - 脚本不会执行 docker compose down -v。
  - upgrade 要求 Git 工作区干净，避免覆盖本地修改。
  - 数据库由 Alembic 自动迁移；升级前仍须备份并审查迁移脚本。
EOF
}

log() {
    printf '[%(%F %T)T] %s\n' -1 "$*"
}

fail() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

preflight() {
    [[ ${EUID} -ne 0 ]] || fail "请使用部署用户运行，不要使用 sudo 执行整个脚本"
    [[ -d "${PROJECT_DIR}" ]] || fail "项目目录不存在：${PROJECT_DIR}"
    [[ -f "${PROJECT_DIR}/compose.yaml" ]] || fail "缺少 compose.yaml"
    [[ -f "${PROJECT_DIR}/.env" ]] || fail "缺少 .env"
    require_command docker
    require_command curl
    require_command systemctl
    cd "${PROJECT_DIR}"
    "${COMPOSE[@]}" version >/dev/null
}

free_mb() {
    df -Pm "${PROJECT_DIR}" | awk 'NR == 2 {print $4}'
}

check_disk() {
    local available
    available="$(free_mb)"
    if (( available < MIN_FREE_MB )); then
        fail "可用磁盘仅 ${available} MB，升级至少需要 ${MIN_FREE_MB} MB"
    fi
    log "磁盘检查通过：可用 ${available} MB"
}

wait_local_health() {
    local i
    for ((i = 1; i <= HEALTH_RETRIES; i++)); do
        if curl --fail --silent --show-error --max-time 5 "${LOCAL_HEALTH_URL}" >/dev/null 2>&1; then
            log "本地健康检查通过：${LOCAL_HEALTH_URL}"
            return 0
        fi
        sleep "${HEALTH_INTERVAL}"
    done
    fail "本地健康检查失败：${LOCAL_HEALTH_URL}"
}

wait_public_health() {
    local i
    for ((i = 1; i <= HEALTH_RETRIES; i++)); do
        if curl --fail --silent --show-error --max-time 5 "${PUBLIC_HEALTH_URL}" >/dev/null 2>&1; then
            curl --fail --silent --show-error --max-time 5 "${PUBLIC_HEALTH_URL}"
            printf '\n'
            log "HTTPS 健康检查通过：${PUBLIC_HEALTH_URL}"
            return 0
        fi
        sleep "${HEALTH_INTERVAL}"
    done
    fail "HTTPS 健康检查失败：${PUBLIC_HEALTH_URL}"
}

app_start() {
    log "启动 Compose 应用"
    "${COMPOSE[@]}" up -d
    wait_local_health
}

app_stop() {
    log "停止 Compose 应用（保留容器和数据卷）"
    "${COMPOSE[@]}" stop
}

app_restart() {
    log "重启 Compose 应用"
    "${COMPOSE[@]}" restart
    wait_local_health
}

proxy_start() {
    wait_local_health
    log "启动 HTTPS 代理：${PROXY_SERVICE}"
    systemctl --user start "${PROXY_SERVICE}"
    systemctl --user is-active --quiet "${PROXY_SERVICE}" || fail "HTTPS 代理启动失败"
}

proxy_stop() {
    log "停止 HTTPS 代理：${PROXY_SERVICE}"
    systemctl --user stop "${PROXY_SERVICE}"
}

proxy_restart() {
    wait_local_health
    log "重启 HTTPS 代理：${PROXY_SERVICE}"
    systemctl --user restart "${PROXY_SERVICE}"
    systemctl --user is-active --quiet "${PROXY_SERVICE}" || fail "HTTPS 代理重启失败"
    wait_public_health
}

start_all() {
    app_start
    proxy_start
    wait_public_health
}

stop_all() {
    proxy_stop
    app_stop
}

restart_all() {
    proxy_stop
    app_restart
    proxy_start
    wait_public_health
}

show_status() {
    printf '\n=== Docker Compose ===\n'
    "${COMPOSE[@]}" ps || true
    printf '\n=== HTTPS 代理 ===\n'
    systemctl --user --no-pager --full status "${PROXY_SERVICE}" || true
    printf '\n=== 关键端口 ===\n'
    if command -v ss >/dev/null 2>&1; then
        ss -ltn '( sport = :443 or sport = :8080 )' || true
    fi
    printf '\n=== 磁盘 ===\n'
    df -h "${PROJECT_DIR}"
    printf '\n=== 健康检查 ===\n'
    if curl --fail --silent --show-error --max-time 5 "${LOCAL_HEALTH_URL}"; then
        printf '\n本地：正常\n'
    else
        printf '\n本地：异常\n'
    fi
    if curl --fail --silent --show-error --max-time 15 "${PUBLIC_HEALTH_URL}"; then
        printf '\nHTTPS：正常\n'
    else
        printf '\nHTTPS：异常\n'
    fi
}

show_logs() {
    local service="${1:-}"
    if [[ "${service}" == "proxy" ]]; then
        exec journalctl --user -u "${PROXY_SERVICE}" -f
    elif [[ -n "${service}" ]]; then
        exec "${COMPOSE[@]}" logs -f "${service}"
    else
        exec "${COMPOSE[@]}" logs -f api web postgres
    fi
}

show_logs_tail() {
    local service="${1:-}"
    if [[ "${service}" == "proxy" ]]; then
        journalctl --user -u "${PROXY_SERVICE}" -n 200 --no-pager
    elif [[ -n "${service}" ]]; then
        "${COMPOSE[@]}" logs --tail 200 "${service}"
    else
        "${COMPOSE[@]}" logs --tail 200 api web postgres
    fi
}

backup_database() {
    local label="${1:-$(date +%Y%m%d-%H%M%S)}"
    local output
    [[ "${label}" =~ ^[A-Za-z0-9._-]+$ ]] || fail "备份名称只能包含字母、数字、点、下划线和连字符"
    umask 077
    mkdir -p "${BACKUP_DIR}"
    output="${BACKUP_DIR}/postgres-${label}.dump"
    [[ ! -e "${output}" ]] || fail "备份文件已存在：${output}"
    "${COMPOSE[@]}" exec -T postgres sh -c \
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"${output}"
    [[ -s "${output}" ]] || fail "数据库备份为空：${output}"
    log "数据库备份完成：${output}"
}

build_images() {
    check_disk
    log "拉取基础镜像并构建应用镜像"
    "${COMPOSE[@]}" build --pull
}

deploy_current() {
    check_disk
    "${COMPOSE[@]}" config --quiet
    build_images
    log "部署当前代码"
    "${COMPOSE[@]}" up -d
    wait_local_health
    proxy_restart
    "${COMPOSE[@]}" ps
    log "部署完成"
}

upgrade() {
    local ref="${1:-}"
    require_command git
    check_disk
    [[ -z "$(git status --porcelain)" ]] || fail "Git 工作区存在未提交修改，请先提交或处理后再升级"

    backup_database "pre-upgrade-$(date +%Y%m%d-%H%M%S)"
    log "获取远程版本"
    git fetch --all --tags --prune

    if [[ -n "${ref}" ]]; then
        log "切换到指定分支、标签或提交：${ref}"
        git checkout "${ref}"
        if git symbolic-ref -q HEAD >/dev/null; then
            git pull --ff-only
        fi
    else
        git symbolic-ref -q HEAD >/dev/null || fail "当前为 detached HEAD，请显式提供升级目标"
        log "快进更新当前分支"
        git pull --ff-only
    fi

    deploy_current
    log "升级完成，当前版本：$(git rev-parse --short HEAD)"
}

config_check() {
    "${COMPOSE[@]}" config --quiet
    /usr/bin/python3.11 -m py_compile "${PROJECT_DIR}/deploy/https_proxy.py"
    systemd-analyze --user verify "${HOME}/.config/systemd/user/${PROXY_SERVICE}"
    log "配置检查通过"
}

db_shell() {
    exec "${COMPOSE[@]}" exec postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

db_counts() {
    "${COMPOSE[@]}" exec -T postgres sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM sources) AS sources, (SELECT count(*) FROM source_endpoints) AS source_endpoints, (SELECT count(*) FROM content_items) AS content_items, (SELECT count(*) FROM fetch_runs) AS fetch_runs;"'
}

main() {
    local command="${1:-help}"
    shift || true

    case "${command}" in
        help|-h|--help)
            usage
            return 0
            ;;
    esac

    preflight

    case "${command}" in
        start) start_all ;;
        stop) stop_all ;;
        restart) restart_all ;;
        app-start) app_start ;;
        app-stop) app_stop ;;
        app-restart) app_restart ;;
        proxy-start) proxy_start ;;
        proxy-stop) proxy_stop ;;
        proxy-restart) proxy_restart ;;
        status) show_status ;;
        health) wait_local_health; wait_public_health ;;
        logs) show_logs "${1:-}" ;;
        logs-tail) show_logs_tail "${1:-}" ;;
        db-shell) db_shell ;;
        db-counts) db_counts ;;
        build) build_images ;;
        deploy) deploy_current ;;
        upgrade) upgrade "${1:-}" ;;
        backup) backup_database "${1:-}" ;;
        config-check) config_check ;;
        *) usage; fail "未知命令：${command}" ;;
    esac
}

main "$@"
