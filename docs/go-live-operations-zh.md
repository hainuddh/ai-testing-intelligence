# AI Testing Intelligence 上线过程与运维记录

> 本文记录当前服务器的实际上线过程、运行结构、验证命令和已知风险，供后续运维人员接手。通用安装、备份和恢复流程参见 `docs/deployment-zh.md`。

## 1. 上线信息

- 项目：AI Testing Intelligence
- 项目目录：`/home/admin/ai-testing-intelligence`
- 部署方式：Docker Compose
- Git 分支：`main`
- 公网服务器：`39.102.75.100`
- 公网 HTTPS 域名：`api.ddhlf.xyz`
- 容器 Web 入口：`127.0.0.1:8080`
- 公网 HTTPS 入口：`0.0.0.0:443`
- 健康接口：`/api/v1/health`

上线前，`api.ddhlf.xyz:443` 由 work-tracker 项目的 Python HTTPS 代理占用。work-tracker 已确认可以下线，因此原代理被停用，域名和 443 端口迁移给本项目。

## 2. 当前访问链路

```text
用户浏览器
  |
  | HTTPS https://api.ddhlf.xyz:443
  v
宿主机 Nginx（1 个 Worker）
  |
  | HTTP http://127.0.0.1:8080
  v
Docker Compose web 容器（Nginx）
  |-- /             -> React 前端静态资源
  `-- /api/*        -> Docker 内部 api:8000
                         |
                         `-> PostgreSQL / Redis
```

PostgreSQL、Redis、MinIO 和 FastAPI 端口均未直接向公网开放。宿主机的 `8080` 只绑定到回环地址。

## 3. 环境准备

项目根目录已有 `.env`，权限设置为 `600`。变量包括：

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `MINIO_ROOT_USER`
- `MINIO_ROOT_PASSWORD`
- `ATI_JWT_SECRET`

`.env` 中另有可选缓存配置（`ATI_REDIS_URL`、`ATI_CONTENT_CACHE_TTL` 等），默认值已适配 2 GB 内存服务器，详见 `docs/deployment-zh.md`。

禁止将 `.env` 内容写入文档、Git、聊天记录或工单。

配置校验：

```bash
cd /home/admin/ai-testing-intelligence
docker compose config --quiet
```

无输出且退出码为 `0` 表示配置有效。

## 4. Docker Compose 启动过程

本次按安装文档执行：

```bash
cd /home/admin/ai-testing-intelligence
docker compose up -d --build
```

当前 Compose 服务：

| 服务 | 作用 | 对宿主机开放 |
| --- | --- | --- |
| `web` | Nginx、React 静态资源、API 反向代理 | `127.0.0.1:8080` |
| `api` | FastAPI（异步读路径 + Redis 缓存） | 否，仅 Compose 网络 `8000` |
| `postgres` | PostgreSQL 17 + pgvector | 否 |
| `redis` | Redis 8，缓存列表/详情/统计结果，限 128MB | 否 |
| `minio` | MinIO | 否 |

各服务均已设置 `mem_limit`，PostgreSQL 已收敛 `shared_buffers`/`work_mem`/`max_connections`，适配 2 GB 内存服务器。

检查状态：

```bash
docker compose ps
```

检查本地健康接口：

```bash
curl --fail http://127.0.0.1:8080/api/v1/health
```

预期结果：

```json
{"status":"ok"}
```

## 5. work-tracker 下线与 443 端口迁移

原 systemd 用户服务：

```text
/home/admin/.config/systemd/user/work-tracker-proxy.service
```

原服务监听 `0.0.0.0:443`，并将请求转发到 work-tracker 的 `127.0.0.1:8000`。

本次执行：

```bash
systemctl --user disable --now work-tracker-proxy.service
```

迁移后状态应为：

```bash
systemctl --user is-enabled work-tracker-proxy.service
systemctl --user is-active work-tracker-proxy.service
```

预期分别为 `disabled` 和 `inactive`。不要重新启用原服务，否则会与新代理争抢 443 端口。

本次仅停用 work-tracker 的 HTTPS 代理，没有删除 `/home/admin/work-tracker` 项目目录和数据。确认不再需要后，另行制定归档或删除方案。

## 6. Nginx HTTPS 入口

生产入口已从自定义 Python HTTPS 代理迁移到宿主机 Nginx，Docker内部结构不变。由于 HTTP-01 无法通过，实际迁移使用现有证书并跳过 Certbot：

```bash
cd /home/admin/ai-testing-intelligence
ATI_SKIP_PACKAGE_UPDATE=1 \
ATI_SKIP_CERTBOT_RECONFIGURE=1 \
bash scripts/migrate-to-nginx.sh
```

脚本迁移过程中处理了以下 Alibaba Cloud Linux差异：

- 系统使用 `dnf`，而不是 Debian/Ubuntu的 `apt-get`；
- `nginx` 被 DNF `exclude` 规则过滤，脚本只对该次安装临时使用 `--disableexcludes=all`；
- Nginx站点目录为 `/etc/nginx/conf.d`，而不是 `sites-enabled`；
- SELinux Enforcing环境需要允许 Nginx读取 Webroot并连接 `127.0.0.1:8080`；
- 服务器 Certbot版本较旧，不支持 `reconfigure`。

当前站点配置：

```text
/etc/nginx/conf.d/ai-testing-intelligence.conf
```

低内存配置保持 1 个 Worker、512 个连接、8 条上游保活连接和 1 MB SSL Session Cache。旧 `ai-testing-intelligence-proxy.service` 已停止并禁用，不与 Nginx同时常驻。

日常检查：

```bash
sudo /usr/sbin/nginx -t
sudo systemctl is-active nginx
sudo systemctl is-enabled nginx
systemctl --user is-active ai-testing-intelligence-proxy.service
systemctl --user is-enabled ai-testing-intelligence-proxy.service
sudo ss -ltnp '( sport = :80 or sport = :443 or sport = :8080 )'
ps -C nginx -o pid,rss,cmd
```

预期 Nginx为 `active`、`enabled`，Python代理为 `inactive`、`disabled`，80/443 由 Nginx监听，8080 仍只绑定 `127.0.0.1`。

旧 Python代理保留为应急回退实现：

```text
/home/admin/ai-testing-intelligence/deploy/https_proxy.py
/home/admin/.config/systemd/user/ai-testing-intelligence-proxy.service
```

## 7. DNS-01 证书签发经验

### 7.1 HTTP-01 失败结论

迁移过程依次确认了 Nginx配置、本机 HTTPS、Webroot、SELinux上下文和旧版 Certbot兼容流程正常，但 Let's Encrypt从外部访问以下地址持续收到 HTTP `403`：

```text
http://api.ddhlf.xyz/.well-known/acme-challenge/<token>
```

ICP备案系统未查询到 `ddhlf.xyz`。服务器位于中国大陆节点，因此不再依赖公网 80 和 HTTP-01，也不让证书续期阻塞 Nginx迁移。

### 7.2 最终签发方案

最终采用 `acme.sh + AliDNS DNS-01`：

- 创建专用阿里云 RAM API用户并授予最小 AliDNS记录管理权限；
- AccessKey只保存在 root 的 acme.sh配置中，文件权限为 `600`；
- 使用 `dns_ali` 自动创建和清理 `_acme-challenge.api.ddhlf.xyz` TXT记录；
- 先使用 Staging验证 DNS API，再签发正式 ECDSA P-256证书；
- acme.sh不常驻内存，只在签发或续期时短暂运行，适合 2 GB服务器。

关键命令：

```bash
# 测试 DNS API；Ali_Key/Ali_Secret必须在同一个 root Shell中加载
/root/.acme.sh/acme.sh --issue --staging --dns dns_ali -d api.ddhlf.xyz

# 正式签发；从 Staging切换到正式证书时只使用一次 --force
/root/.acme.sh/acme.sh --issue --server letsencrypt \
  --dns dns_ali -d api.ddhlf.xyz --keylength ec-256 --force

/root/.acme.sh/acme.sh --list
```

如果输出 `Domains not changed` 和下次续期时间，不代表失败；先检查已有证书是 Staging还是真实 Let’s Encrypt。不要反复使用 `--force`，避免消耗正式签发额度。

证书应通过 `--install-cert` 安装到 Nginx固定目录，不能让 Nginx直接读取 `/root/.acme.sh`：

```text
/etc/nginx/ssl/api.ddhlf.xyz/fullchain.pem
/etc/nginx/ssl/api.ddhlf.xyz/privkey.pem
```

安装时设置 `--reloadcmd "systemctl reload nginx"`，使续期后的证书复制和 Nginx Reload自动完成。

### 7.3 成功判定与长期维护

本次基本检查已经通过。后续使用以下命令复核：

```bash
/root/.acme.sh/acme.sh --list
sudo crontab -l
sudo /usr/sbin/nginx -t
curl --fail https://api.ddhlf.xyz/api/v1/health
openssl x509 -in /etc/nginx/ssl/api.ddhlf.xyz/fullchain.pem \
  -noout -issuer -serial -dates
echo | openssl s_client -connect api.ddhlf.xyz:443 \
  -servername api.ddhlf.xyz 2>/dev/null \
  | openssl x509 -noout -issuer -serial -dates
```

磁盘证书与线上证书的序列号和有效期必须一致，root Cron中应存在 acme.sh `--cron` 任务。第一次自然续期后，再确认 AliDNS TXT记录自动创建/删除、证书复制和 Nginx Reload均成功。

旧 Certbot配置暂时保留作为历史回退，但不再作为主续期方案；确认 acme.sh长期稳定前不要删除 `/etc/letsencrypt`。严禁把 RAM AccessKey、Secret、`account.conf` 内容或调试日志中的签名参数写入 Git、文档、聊天或工单。

### 7.4 证书临期或过期处理

正常情况下不应等到证书过期再处理。root Cron每天调用 acme.sh检查续期窗口，只有接近续期时间时才通过 AliDNS创建 TXT记录、签发证书、复制到 Nginx固定目录并执行 Reload；acme.sh不是常驻服务，不会持续占用内存。

首先确认自动续期的三个前提：

```bash
sudo crontab -l
/root/.acme.sh/acme.sh --list
grep -E '^(Le_RealFullChainPath|Le_RealKeyPath|Le_ReloadCmd)=' \
  /root/.acme.sh/api.ddhlf.xyz_ecc/api.ddhlf.xyz.conf 2>/dev/null
```

预期 root Cron存在 acme.sh `--cron` 任务，域名记录显示下次续期时间，安装路径指向 `/etc/nginx/ssl/api.ddhlf.xyz`，Reload命令为 `systemctl reload nginx`。AliDNS凭据必须已保存且 `account.conf` 权限为 `600`，但检查时只能确认变量名，不得打印真实值：

```bash
grep -E '^(SAVED_)?Ali_(Key|Secret)=' /root/.acme.sh/account.conf \
  | sed 's/=.*/=<已隐藏>/'
stat -c '%a %U:%G %n' /root/.acme.sh/account.conf
```

日常检查剩余有效期：

```bash
openssl x509 -in /etc/nginx/ssl/api.ddhlf.xyz/fullchain.pem \
  -noout -issuer -dates
echo | openssl s_client -connect api.ddhlf.xyz:443 \
  -servername api.ddhlf.xyz 2>/dev/null \
  | openssl x509 -noout -issuer -dates
```

如果证书临近到期但自动续期未执行，先运行 Cron流程并观察错误，不要直接反复强制签发：

```bash
/root/.acme.sh/acme.sh --cron --home /root/.acme.sh --debug 2
```

重点检查 RAM AccessKey状态、AliDNS权限、域名托管账号、服务器时间、证书安装路径和 Nginx Reload。确需紧急恢复时只执行一次强制续期：

```bash
/root/.acme.sh/acme.sh --renew -d api.ddhlf.xyz --ecc --force
sudo /usr/sbin/nginx -t
sudo systemctl reload nginx
curl --fail https://api.ddhlf.xyz/api/v1/health
```

如果证书已经过期，DNS-01仍可签发新证书，因为它不依赖旧 HTTPS证书或公网 80：

```bash
/root/.acme.sh/acme.sh --issue --server letsencrypt \
  --dns dns_ali -d api.ddhlf.xyz --keylength ec-256 --force
/root/.acme.sh/acme.sh --install-cert -d api.ddhlf.xyz --ecc \
  --key-file /etc/nginx/ssl/api.ddhlf.xyz/privkey.pem \
  --fullchain-file /etc/nginx/ssl/api.ddhlf.xyz/fullchain.pem \
  --reloadcmd "systemctl reload nginx"
```

恢复后必须重新比较磁盘和线上证书序列号，并确认 HTTPS健康接口正常。`--force`会消耗正式签发额度，只用于首次从 Staging切换或紧急恢复，不得加入日常 Cron。

建议配置证书剩余 15 天告警。仅写入系统日志但无人接收不算有效告警，应接入阿里云云监控、邮件、钉钉、企业微信或现有告警平台。

## 8. 上线验证记录

完整项目验证使用：

```bash
cd /home/admin/ai-testing-intelligence
hermes verify --json --port 8080
```

注意：Hermes 自动探测曾默认检查宿主机 `8000`，导致首次验证失败。该端口只存在于 Compose 内部，宿主机实际入口是 `8080`，所以必须指定：

```text
--port 8080
```

使用正确端口后验证结果：

- 命令退出码：`0`
- API 镜像构建成功
- Web 镜像构建成功
- Readiness URL：`http://127.0.0.1:8080/`
- Readiness：`ready: true`
- HTTP 状态：`200`

由于 `hermes verify` 会启动并清理测试环境，执行后应再次确保长期运行的 Compose 服务已启动：

```bash
docker compose up -d
```

最终检查：

```bash
# Compose 服务
docker compose ps

# 本地健康检查
curl --fail http://127.0.0.1:8080/api/v1/health

# 在服务器上强制解析到本机，验证 HTTPS、证书和代理链路
curl --resolve api.ddhlf.xyz:443:127.0.0.1 \
  --fail https://api.ddhlf.xyz/api/v1/health

# HTTPS 入口与旧代理
sudo systemctl is-active nginx
sudo systemctl is-enabled nginx
systemctl --user is-active ai-testing-intelligence-proxy.service
systemctl --user is-enabled ai-testing-intelligence-proxy.service
```

预期：两个健康检查都返回 `{"status":"ok"}`；Nginx返回 `active`、`enabled`，旧 Python 代理返回 `inactive`、`disabled`。

## 9. 运维脚本与日常命令

项目提供统一运维脚本：

```text
/home/admin/ai-testing-intelligence/scripts/ops.sh
```

常用操作：

```bash
cd /home/admin/ai-testing-intelligence

# 查看帮助、状态与健康检查
./scripts/ops.sh --help
./scripts/ops.sh status
./scripts/ops.sh health

# 启停整个服务（应用和 HTTPS 代理）
./scripts/ops.sh start
./scripts/ops.sh stop
./scripts/ops.sh restart

# 分别管理应用和代理
./scripts/ops.sh app-start
./scripts/ops.sh app-stop
./scripts/ops.sh app-restart
./scripts/ops.sh proxy-start
./scripts/ops.sh proxy-stop
./scripts/ops.sh proxy-restart

# 日志和数据库
./scripts/ops.sh logs api
./scripts/ops.sh logs-tail proxy
./scripts/ops.sh db-counts
./scripts/ops.sh db-shell

# 备份、部署与升级
./scripts/ops.sh backup manual-before-change
./scripts/ops.sh deploy
./scripts/ops.sh upgrade main
```

`upgrade` 会先检查磁盘和 Git 工作区，再备份 PostgreSQL、拉取目标版本、构建镜像、部署并执行本地及 HTTPS 健康检查。它要求 Git 工作区干净，并且不会执行 `docker compose down -v`。数据库由 `migrate` 服务执行 Alembic 迁移；涉及表结构变化时仍须提前审查迁移脚本和备份数据库。

升级后若引入了缓存逻辑变更（如读路径缓存版本号），建议手动重启 API 与 Worker 确保缓存一致：

```bash
docker compose restart api worker
```

构建加速说明：所有依赖已锁定精确版本；Dockerfile 使用 `--mount=type=cache` 持久化 pip/npm 下载缓存，源码未变化时依赖层快速复用。默认使用国内镜像源（阿里云 PyPI / 淘宝 NPM），可通过 `PIP_INDEX_URL`、`NPM_CONFIG_REGISTRY` 环境变量覆盖。基础镜像标签固定，不跟随 `latest` 浮动。

底层日常命令：

```bash
cd /home/admin/ai-testing-intelligence

# 查看全部容器
docker compose ps

# 查看近期日志
docker compose logs --tail 200

# 查看关键服务日志
docker compose logs --tail 200 api web postgres

# 重启应用容器
docker compose restart api web

# 停止但保留数据卷
docker compose down

# 重新启动
docker compose up -d

# 健康检查
curl --fail http://127.0.0.1:8080/api/v1/health

# HTTPS 入口检查
curl --fail https://api.ddhlf.xyz/api/v1/health
```

严禁在需要保留数据时执行：

```bash
docker compose down -v
```

该命令会删除 PostgreSQL、Redis 和 MinIO 数据卷。

## 10. 故障排查顺序

### 公网域名打不开

```bash
sudo systemctl status nginx
sudo ss -ltnp '( sport = :443 )'
curl --resolve api.ddhlf.xyz:443:127.0.0.1 -v \
  https://api.ddhlf.xyz/api/v1/health
```

若本机强制解析可以访问而公网不能访问，重点检查 DNS、阿里云安全组、WAF/CDN 和证书状态。

### 代理提示后端不可达

```bash
docker compose ps
curl --fail http://127.0.0.1:8080/api/v1/health
docker compose logs --tail 200 web api
```

### 443 端口被占用

```bash
sudo ss -ltnp '( sport = :443 )'
systemctl --user is-active work-tracker-proxy.service
```

确保原 `work-tracker-proxy.service` 没有被重新启用。

### 数据库或容器异常

```bash
docker compose ps
docker compose logs --tail 300 postgres api
df -h / /var/lib/docker
```

## 11. 当前风险与待办

1. **磁盘容量风险**：上线期间根分区曾达到约 `98%`，PostgreSQL 日志出现过 `No space left on device`。应立即清理无用日志、Docker 缓存和旧文件，并设置磁盘监控；清理前不要误删数据库卷。
2. **证书续期验证**：DNS-01 基本签发检查已通过，但第一次自然续期后仍须确认 AliDNS TXT创建/删除、证书安装和 Nginx Reload完整链路。定期检查证书到期时间和 acme.sh Cron日志。
3. **DNS API凭据**：AliDNS RAM凭据只允许 root读取，应保持最小权限并定期轮换；严禁写入项目目录、Git或日志。
4. **旧代理回退**：Python代理仅作为应急回退，不应与 Nginx同时运行，否则会争抢 443。其日志轮转风险只在回退启用期间存在。
5. **数据库迁移**：当前应用已使用 Alembic，`migrate` 服务必须以 `Exited (0)` 完成后 API 和 Worker 才能启动。升级前仍须备份并检查迁移脚本。
6. **缓存依赖 Redis**：读路径列表/详情/统计依赖 Redis 缓存。Redis 故障时读路径自动降级直查数据库，功能可用但会变慢；升级或扩容 Redis 时留意 `ATI_REDIS_URL`。
7. **低内存运维**：服务器仅 2 GB 内存。升级后检查 `docker stats` 确认各容器内存未持续触顶；若 Postgres 内存紧张，优先检查长事务和慢查询，不要盲目调大 `shared_buffers`。

## 12. 上线完成判定

只有以下条件全部满足，才视为服务正常上线：

- `docker compose ps -a` 中 API、Worker、Web、PostgreSQL、Redis、MinIO 均运行，`migrate` 为 `Exited (0)`；
- PostgreSQL、Redis 为 `healthy`；
- `curl http://127.0.0.1:8080/api/v1/health` 返回 `{"status":"ok"}`；
- 宿主机 Nginx为 `active`、`enabled`，`ai-testing-intelligence-proxy.service` 为 `inactive`、`disabled`；
- HTTPS 健康接口返回 `{"status":"ok"}`；
- `work-tracker-proxy.service` 为 `inactive`、`disabled`；
- 磁盘有足够可用空间；
- acme.sh DNS-01 正式证书已安装，线上序列号与磁盘证书一致，root Cron存在；
- Redis 缓存可达：`docker compose exec api python -c "from app.cache import get_json; import asyncio; print(asyncio.run(get_json('health:check')))"` 不报错即可（返回 `None` 正常，Redis 可达且降级未触发即算通过）。
