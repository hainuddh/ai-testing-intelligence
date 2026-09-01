#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${ATI_PROJECT_DIR:-/home/admin/ai-testing-intelligence}"
DOMAIN="${ATI_DOMAIN:-api.ddhlf.xyz}"
LEGACY_PROXY_SERVICE="${ATI_PROXY_SERVICE:-ai-testing-intelligence-proxy.service}"
LOCAL_HEALTH_URL="${ATI_LOCAL_HEALTH_URL:-http://127.0.0.1:8080/api/v1/health}"
PUBLIC_HEALTH_URL="${ATI_PUBLIC_HEALTH_URL:-https://${DOMAIN}/api/v1/health}"
CERT_DIR="${ATI_CERT_DIR:-/etc/letsencrypt/live/${DOMAIN}}"
ACME_ROOT="${ATI_ACME_ROOT:-/var/www/certbot}"
SITE_NAME="ai-testing-intelligence.conf"
SITE_AVAILABLE=""
SITE_ENABLED=""
NGINX_CONF="/etc/nginx/nginx.conf"
NGINX_BIN="${ATI_NGINX_BIN:-}"
PROXY_MARKER="${ATI_NGINX_MARKER:-/etc/ai-testing-intelligence/nginx-proxy.enabled}"
CERTBOT_HOOK="/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh"
BACKUP_ROOT="${ATI_NGINX_BACKUP_DIR:-${PROJECT_DIR}/backups/nginx-migration}"
HEALTH_RETRIES="${ATI_HEALTH_RETRIES:-15}"
HEALTH_INTERVAL="${ATI_HEALTH_INTERVAL:-2}"
MIN_FREE_MB="${ATI_MIN_FREE_MB:-512}"
MIN_AVAILABLE_MEMORY_MB="${ATI_MIN_AVAILABLE_MEMORY_MB:-128}"

BACKUP_DIR=""
LEGACY_WAS_ACTIVE=0
LEGACY_WAS_ENABLED=0
NGINX_WAS_ACTIVE=0
NGINX_WAS_ENABLED=0
SITE_WAS_ENABLED=0
MARKER_WAS_PRESENT=0
CERTBOT_HOOK_WAS_PRESENT=0
MIGRATION_COMPLETE=0
PACKAGE_MANAGER=""
SELINUX_BOOLEAN_CHANGED=0

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

usage() {
    cat <<'EOF'
将 AI Testing Intelligence 从 Python HTTPS 代理迁移到低内存 Nginx。

用法：
  ./scripts/migrate-to-nginx.sh

常用覆盖变量：
  ATI_PROJECT_DIR=/home/admin/ai-testing-intelligence
  ATI_DOMAIN=api.ddhlf.xyz
  ATI_CERT_DIR=/etc/letsencrypt/live/api.ddhlf.xyz
  ATI_SKIP_PACKAGE_UPDATE=1          跳过软件包索引更新
  ATI_SKIP_CERTBOT_RECONFIGURE=1    跳过 Certbot webroot 迁移

脚本会安装 Nginx、备份原配置、写入低内存配置、切换 443、执行健康检查，
并仅在成功后禁用旧 Python 代理。切换失败时会自动恢复配置和旧代理。
EOF
}

preflight() {
    [[ ${EUID} -ne 0 ]] || fail "请使用部署用户运行，不要使用 sudo 执行整个脚本"
    [[ $# -eq 0 ]] || fail "不支持位置参数，请通过 ATI_* 环境变量覆盖配置"
    [[ "${DOMAIN}" =~ ^([A-Za-z0-9][A-Za-z0-9-]*\.)+[A-Za-z]{2,63}$ ]] || fail "域名格式无效：${DOMAIN}"
    [[ -d "${PROJECT_DIR}" ]] || fail "项目目录不存在：${PROJECT_DIR}"
    [[ -f "${PROJECT_DIR}/compose.yaml" ]] || fail "缺少 compose.yaml"
    require_command sudo
    require_command curl
    require_command systemctl
    require_command ss
    require_command awk
    require_command sed
    [[ "${HEALTH_RETRIES}" =~ ^[1-9][0-9]*$ ]] || fail "ATI_HEALTH_RETRIES 必须是正整数"
    [[ "${HEALTH_INTERVAL}" =~ ^[1-9][0-9]*$ ]] || fail "ATI_HEALTH_INTERVAL 必须是正整数"
    [[ "${MIN_FREE_MB}" =~ ^[1-9][0-9]*$ ]] || fail "ATI_MIN_FREE_MB 必须是正整数"
    [[ "${MIN_AVAILABLE_MEMORY_MB}" =~ ^[1-9][0-9]*$ ]] \
        || fail "ATI_MIN_AVAILABLE_MEMORY_MB 必须是正整数"
    sudo -v
    sudo test -f "${CERT_DIR}/fullchain.pem" || fail "缺少证书：${CERT_DIR}/fullchain.pem"
    sudo test -f "${CERT_DIR}/privkey.pem" || fail "缺少私钥：${CERT_DIR}/privkey.pem"

    curl --fail --silent --show-error --max-time 5 "${LOCAL_HEALTH_URL}" >/dev/null \
        || fail "迁移前本地应用健康检查失败：${LOCAL_HEALTH_URL}"

    local free_mb available_memory_mb
    free_mb="$(df -Pm "${PROJECT_DIR}" | awk 'NR == 2 {print $4}')"
    available_memory_mb="$(awk '/^MemAvailable:/ {printf "%d", $2 / 1024}' /proc/meminfo)"
    (( free_mb >= MIN_FREE_MB )) || fail "可用磁盘仅 ${free_mb} MB，迁移至少需要 ${MIN_FREE_MB} MB"
    (( available_memory_mb >= MIN_AVAILABLE_MEMORY_MB )) \
        || fail "可用内存仅 ${available_memory_mb} MB，迁移至少需要 ${MIN_AVAILABLE_MEMORY_MB} MB"
    log "资源检查通过：磁盘 ${free_mb} MB，可用内存 ${available_memory_mb} MB"

    if command -v apt-get >/dev/null 2>&1; then
        PACKAGE_MANAGER="apt-get"
    elif command -v dnf >/dev/null 2>&1; then
        PACKAGE_MANAGER="dnf"
    elif command -v yum >/dev/null 2>&1; then
        PACKAGE_MANAGER="yum"
    elif [[ -z "${NGINX_BIN}" || ! -x "${NGINX_BIN}" ]]; then
        fail "未找到 apt-get、dnf 或 yum，无法自动安装 Nginx"
    fi

    if sudo systemctl is-active --quiet nginx 2>/dev/null; then
        NGINX_WAS_ACTIVE=1
    fi
    if sudo systemctl is-enabled --quiet nginx 2>/dev/null; then
        NGINX_WAS_ENABLED=1
    fi
    if systemctl --user is-active --quiet "${LEGACY_PROXY_SERVICE}" 2>/dev/null; then
        LEGACY_WAS_ACTIVE=1
    fi
    if systemctl --user is-enabled --quiet "${LEGACY_PROXY_SERVICE}" 2>/dev/null; then
        LEGACY_WAS_ENABLED=1
    fi
    if sudo test -f "${PROXY_MARKER}"; then
        MARKER_WAS_PRESENT=1
    fi
    if sudo test -f "${CERTBOT_HOOK}"; then
        CERTBOT_HOOK_WAS_PRESENT=1
    fi
}

install_nginx() {
    if [[ -z "${NGINX_BIN}" ]]; then
        NGINX_BIN="$(command -v nginx 2>/dev/null || true)"
        [[ -n "${NGINX_BIN}" ]] || NGINX_BIN="/usr/sbin/nginx"
    fi
    if [[ -n "${NGINX_BIN}" ]] && sudo test -x "${NGINX_BIN}"; then
        log "Nginx 已安装，跳过安装"
    else
        case "${PACKAGE_MANAGER}" in
            apt-get)
                if [[ "${ATI_SKIP_PACKAGE_UPDATE:-${ATI_SKIP_APT_UPDATE:-0}}" != "1" ]]; then
                    log "更新 APT 软件索引"
                    sudo apt-get update
                fi
                log "通过 APT 安装最小化 Nginx 包"
                sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx
                ;;
            dnf)
                if [[ "${ATI_SKIP_PACKAGE_UPDATE:-0}" != "1" ]]; then
                    log "刷新 DNF 软件仓库缓存"
                    sudo dnf makecache
                fi
                log "通过 DNF 安装 Nginx 包"
                if ! sudo dnf install -y nginx; then
                    log "普通 DNF 安装失败，临时忽略软件包排除规则重试（不修改永久配置）"
                    sudo dnf --disableexcludes=all install -y nginx
                fi
                ;;
            yum)
                if [[ "${ATI_SKIP_PACKAGE_UPDATE:-0}" != "1" ]]; then
                    log "刷新 YUM 软件仓库缓存"
                    sudo yum makecache
                fi
                log "通过 YUM 安装 Nginx 包"
                if ! sudo yum install -y nginx; then
                    log "普通 YUM 安装失败，临时忽略软件包排除规则重试（不修改永久配置）"
                    sudo yum --disableexcludes=all install -y nginx
                fi
                ;;
            *)
                fail "无法识别可用的软件包管理器"
                ;;
        esac
    fi

    sudo test -x "${NGINX_BIN}" || fail "Nginx 安装失败"

    if sudo test -d /etc/nginx/sites-available && sudo test -d /etc/nginx/sites-enabled; then
        SITE_AVAILABLE="/etc/nginx/sites-available/${SITE_NAME}"
        SITE_ENABLED="/etc/nginx/sites-enabled/${SITE_NAME}"
    elif sudo test -d /etc/nginx/conf.d; then
        SITE_AVAILABLE="/etc/nginx/conf.d/${SITE_NAME}"
        SITE_ENABLED="${SITE_AVAILABLE}"
    else
        fail "无法识别 Nginx 站点配置目录"
    fi
    if sudo test -e "${SITE_ENABLED}" || sudo test -L "${SITE_ENABLED}"; then
        SITE_WAS_ENABLED=1
    fi
    log "Nginx 布局：二进制 ${NGINX_BIN}，站点配置 ${SITE_ENABLED}"
}

backup_nginx() {
    BACKUP_DIR="${BACKUP_ROOT}/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "${BACKUP_DIR}"
    chmod 700 "${BACKUP_DIR}"
    sudo cp -a "${NGINX_CONF}" "${BACKUP_DIR}/nginx.conf"
    if sudo test -e "${SITE_AVAILABLE}"; then
        sudo cp -a "${SITE_AVAILABLE}" "${BACKUP_DIR}/site.available"
    fi
    if (( SITE_WAS_ENABLED )) && [[ "${SITE_ENABLED}" != "${SITE_AVAILABLE}" ]]; then
        sudo cp -a "${SITE_ENABLED}" "${BACKUP_DIR}/site.enabled"
    fi
    if sudo test -f "/etc/letsencrypt/renewal/${DOMAIN}.conf"; then
        sudo cp -a "/etc/letsencrypt/renewal/${DOMAIN}.conf" "${BACKUP_DIR}/certbot-renewal.conf"
    fi
    if (( CERTBOT_HOOK_WAS_PRESENT )); then
        sudo cp -a "${CERTBOT_HOOK}" "${BACKUP_DIR}/certbot-deploy-hook"
    fi
    if sudo test -e /etc/nginx/sites-enabled/default || sudo test -L /etc/nginx/sites-enabled/default; then
        sudo cp -a /etc/nginx/sites-enabled/default "${BACKUP_DIR}/default.enabled"
    fi
    log "Nginx 配置已备份到 ${BACKUP_DIR}"
}

configure_nginx() {
    local temp_conf temp_site
    temp_conf="$(mktemp)"
    temp_site="$(mktemp)"

    sudo cat "${NGINX_CONF}" >"${temp_conf}"
    if grep -Eq '^[[:space:]]*worker_processes[[:space:]]+' "${temp_conf}"; then
        sed -Ei 's/^[[:space:]]*worker_processes[[:space:]]+[^;]+;/worker_processes 1;/' "${temp_conf}"
    else
        sed -i '1i worker_processes 1;' "${temp_conf}"
    fi
    sed -Ei 's/^[[:space:]]*worker_connections[[:space:]]+[0-9]+;/    worker_connections 512;/' "${temp_conf}"
    grep -Eq '^worker_processes 1;' "${temp_conf}" || fail "无法设置 Nginx worker_processes"
    grep -Eq '^[[:space:]]*worker_connections 512;' "${temp_conf}" || fail "无法设置 Nginx worker_connections"
    sudo install -o root -g root -m 0644 "${temp_conf}" "${NGINX_CONF}"

    cat >"${temp_site}" <<EOF
upstream ati_web {
    server 127.0.0.1:8080;
    keepalive 8;
}

limit_req_zone \$binary_remote_addr zone=ati_login:1m rate=5r/m;
limit_conn_zone \$binary_remote_addr zone=ati_conn:1m;

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location ^~ /.well-known/acme-challenge/ {
        root ${ACME_ROOT};
        default_type text/plain;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:ATI_SSL:1m;
    ssl_session_timeout 1h;
    ssl_session_tickets off;

    server_tokens off;
    access_log /var/log/nginx/ai-testing-intelligence-access.log combined buffer=16k flush=1m;
    error_log /var/log/nginx/ai-testing-intelligence-error.log warn;
    client_max_body_size 2m;
    client_body_buffer_size 16k;
    keepalive_timeout 30s;
    keepalive_requests 100;
    limit_conn ati_conn 20;

    location = /api/v1/auth/login {
        limit_req zone=ati_login burst=5 nodelay;
        proxy_pass http://ati_web;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 3s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location / {
        proxy_pass http://ati_web;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_connect_timeout 3s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 4 8k;
        proxy_busy_buffers_size 16k;
    }
}
EOF

    sudo install -d -o root -g root -m 0755 "${ACME_ROOT}/.well-known/acme-challenge"
    sudo install -o root -g root -m 0644 "${temp_site}" "${SITE_AVAILABLE}"
    if [[ "${SITE_ENABLED}" != "${SITE_AVAILABLE}" ]]; then
        sudo ln -sfn "${SITE_AVAILABLE}" "${SITE_ENABLED}"
        sudo rm -f /etc/nginx/sites-enabled/default
    fi
    sudo "${NGINX_BIN}" -t
    rm -f "${temp_conf}" "${temp_site}"
    log "低内存 Nginx 配置校验通过"
}

configure_selinux() {
    if ! command -v getenforce >/dev/null 2>&1 || [[ "$(getenforce)" != "Enforcing" ]]; then
        return
    fi
    require_command getsebool
    require_command setsebool
    if [[ "$(getsebool httpd_can_network_connect 2>/dev/null | awk '{print $3}')" == "on" ]]; then
        return
    fi
    log "SELinux 为 Enforcing，允许 Nginx 连接本机 Docker Web 上游"
    sudo setsebool -P httpd_can_network_connect 1
    SELINUX_BOOLEAN_CHANGED=1
}

wait_local_tls_health() {
    local i
    for ((i = 1; i <= HEALTH_RETRIES; i++)); do
        if curl --noproxy '*' --resolve "${DOMAIN}:443:127.0.0.1" --fail --silent --show-error \
            --max-time 5 "${PUBLIC_HEALTH_URL}" >/dev/null 2>&1; then
            log "本机 Nginx TLS 健康检查通过：${PUBLIC_HEALTH_URL}"
            return 0
        fi
        sleep "${HEALTH_INTERVAL}"
    done
    return 1
}

rollback() {
    local exit_code=$?
    trap - EXIT
    (( MIGRATION_COMPLETE == 0 )) || exit "${exit_code}"
    printf '迁移失败，开始自动回滚。\n' >&2

    if [[ -n "${BACKUP_DIR}" && -f "${BACKUP_DIR}/nginx.conf" ]]; then
        sudo cp -a "${BACKUP_DIR}/nginx.conf" "${NGINX_CONF}" || true
        if [[ -f "${BACKUP_DIR}/site.available" ]]; then
            sudo cp -a "${BACKUP_DIR}/site.available" "${SITE_AVAILABLE}" || true
        else
            sudo rm -f "${SITE_AVAILABLE}" || true
        fi
        if [[ "${SITE_ENABLED}" != "${SITE_AVAILABLE}" ]]; then
            sudo rm -f "${SITE_ENABLED}" || true
        fi
        if [[ -e "${BACKUP_DIR}/site.enabled" || -L "${BACKUP_DIR}/site.enabled" ]]; then
            sudo cp -a "${BACKUP_DIR}/site.enabled" "${SITE_ENABLED}" || true
        fi
        if [[ -e "${BACKUP_DIR}/default.enabled" || -L "${BACKUP_DIR}/default.enabled" ]]; then
            sudo cp -a "${BACKUP_DIR}/default.enabled" /etc/nginx/sites-enabled/default || true
        fi
        if [[ -f "${BACKUP_DIR}/certbot-renewal.conf" ]]; then
            sudo cp -a "${BACKUP_DIR}/certbot-renewal.conf" \
                "/etc/letsencrypt/renewal/${DOMAIN}.conf" || true
        fi
    fi

    if (( NGINX_WAS_ACTIVE )); then
        sudo "${NGINX_BIN}" -t >/dev/null 2>&1 && sudo systemctl restart nginx || true
    else
        sudo systemctl stop nginx >/dev/null 2>&1 || true
    fi
    if (( NGINX_WAS_ENABLED )); then
        sudo systemctl enable nginx >/dev/null 2>&1 || true
    else
        sudo systemctl disable nginx >/dev/null 2>&1 || true
    fi
    if (( LEGACY_WAS_ENABLED )); then
        systemctl --user enable "${LEGACY_PROXY_SERVICE}" >/dev/null 2>&1 || true
    fi
    if (( LEGACY_WAS_ACTIVE )); then
        systemctl --user start "${LEGACY_PROXY_SERVICE}" >/dev/null 2>&1 || true
    fi
    if (( MARKER_WAS_PRESENT )); then
        sudo install -d -o root -g root -m 0755 "$(dirname "${PROXY_MARKER}")" || true
        printf 'nginx\n' | sudo tee "${PROXY_MARKER}" >/dev/null || true
    else
        sudo rm -f "${PROXY_MARKER}" || true
    fi
    if (( CERTBOT_HOOK_WAS_PRESENT )); then
        sudo cp -a "${BACKUP_DIR}/certbot-deploy-hook" "${CERTBOT_HOOK}" || true
    else
        sudo rm -f "${CERTBOT_HOOK}" || true
    fi
    if (( SELINUX_BOOLEAN_CHANGED )); then
        sudo setsebool -P httpd_can_network_connect 0 || true
    fi
    if (( LEGACY_WAS_ACTIVE )) && ! systemctl --user is-active --quiet "${LEGACY_PROXY_SERVICE}"; then
        printf '警告：旧代理未能恢复，请立即检查服务和 443 端口。\n' >&2
    elif (( NGINX_WAS_ACTIVE )) && ! sudo systemctl is-active --quiet nginx; then
        printf '警告：原 Nginx 未能恢复，请立即检查服务。\n' >&2
    else
        printf '回滚后的入口服务已恢复，请继续核对 HTTPS 健康状态。\n' >&2
    fi
    exit "${exit_code}"
}

switch_proxy() {
    if (( LEGACY_WAS_ACTIVE )); then
        log "停止旧 Python HTTPS 代理"
        systemctl --user stop "${LEGACY_PROXY_SERVICE}"
    fi

    log "启动 Nginx 并切换 80/443 流量"
    sudo systemctl enable nginx >/dev/null
    sudo systemctl restart nginx
    sudo systemctl is-active --quiet nginx || fail "Nginx 启动失败"
    wait_local_tls_health || fail "切换后本机 Nginx TLS 健康检查失败：${PUBLIC_HEALTH_URL}"

    systemctl --user disable "${LEGACY_PROXY_SERVICE}" >/dev/null 2>&1 || true
    log "旧 Python HTTPS 代理已禁用"
}

configure_certbot() {
    local cert_domain cert_domains=() certbot_domain_args=()

    if [[ "${ATI_SKIP_CERTBOT_RECONFIGURE:-0}" == "1" ]]; then
        log "已按配置跳过 Certbot 迁移"
        return
    fi
    if ! command -v certbot >/dev/null 2>&1; then
        fail "未安装 Certbot，无法保证证书续期；如已另行管理证书，请设置 ATI_SKIP_CERTBOT_RECONFIGURE=1"
    fi

    log "将 Certbot 续期方式改为 Nginx webroot"
    if sudo certbot reconfigure --cert-name "${DOMAIN}" --webroot \
        --webroot-path "${ACME_ROOT}" --non-interactive; then
        log "Certbot reconfigure 执行成功"
    else
        log "Certbot reconfigure 不可用或执行失败，使用旧版 certonly 兼容流程"
        require_command openssl
        while IFS= read -r cert_domain; do
            [[ -n "${cert_domain}" ]] && cert_domains+=("${cert_domain}")
        done < <(sudo openssl x509 -in "${CERT_DIR}/fullchain.pem" -noout -ext subjectAltName \
            | grep -oE 'DNS:[^, ]+' | cut -d: -f2)
        (( ${#cert_domains[@]} > 0 )) || fail "无法从现有证书读取 SAN 域名"
        for cert_domain in "${cert_domains[@]}"; do
            certbot_domain_args+=("-d" "${cert_domain}")
        done
        sudo certbot certonly --cert-name "${DOMAIN}" --webroot --webroot-path "${ACME_ROOT}" \
            --force-renewal --non-interactive "${certbot_domain_args[@]}" \
            || fail "Certbot webroot 配置失败；如需暂时跳过，请设置 ATI_SKIP_CERTBOT_RECONFIGURE=1"
    fi

    if sudo certbot renew --cert-name "${DOMAIN}" --dry-run --non-interactive; then
        sudo install -d -o root -g root -m 0755 "$(dirname "${CERTBOT_HOOK}")"
        printf '#!/usr/bin/env sh\nsystemctl reload nginx\n' \
            | sudo tee "${CERTBOT_HOOK}" >/dev/null
        sudo chmod 0755 "${CERTBOT_HOOK}"
        sudo systemctl reload nginx
        log "Certbot webroot、模拟续期和 Nginx reload hook 已配置"
    else
        fail "Certbot 模拟续期失败；请检查公网 80 端口、安全组和 WAF/CDN"
    fi
}

complete_migration() {
    sudo install -d -o root -g root -m 0755 "$(dirname "${PROXY_MARKER}")"
    printf 'nginx\n' | sudo tee "${PROXY_MARKER}" >/dev/null
    MIGRATION_COMPLETE=1
    if curl --fail --silent --show-error --max-time 10 "${PUBLIC_HEALTH_URL}" >/dev/null 2>&1; then
        log "公网 DNS 路径健康检查通过：${PUBLIC_HEALTH_URL}"
    else
        log "警告：本机 Nginx 已验证，但公网 DNS 路径检查失败；请检查 DNS、WAF/CDN 和 NAT 回环"
    fi
}

show_result() {
    printf '\n=== 迁移结果 ===\n'
    sudo systemctl --no-pager --full status nginx || true
    printf '\n=== 监听端口 ===\n'
    sudo ss -ltnp '( sport = :80 or sport = :443 or sport = :8080 )' || true
    printf '\n=== Nginx 进程内存（KiB RSS） ===\n'
    ps -C nginx -o pid,rss,cmd || true
    printf '\n配置备份：%s\n' "${BACKUP_DIR}"
    printf '旧代理状态：'
    systemctl --user is-enabled "${LEGACY_PROXY_SERVICE}" 2>/dev/null || true
    log "迁移完成"
}

main() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        return 0
    fi
    preflight "$@"
    trap rollback EXIT
    install_nginx
    backup_nginx
    configure_nginx
    configure_selinux
    switch_proxy
    configure_certbot
    complete_migration
    show_result
}

main "$@"
