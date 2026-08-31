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
ai-testing-intelligence-proxy.service
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

## 6. AI Testing Intelligence HTTPS 代理

HTTPS 代理脚本：

```text
/home/admin/ai-testing-intelligence/deploy/https_proxy.py
```

systemd 用户服务：

```text
/home/admin/.config/systemd/user/ai-testing-intelligence-proxy.service
```

关键配置：

```text
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8080
LISTEN_PORT=443
CERT_FILE=/etc/letsencrypt/live/api.ddhlf.xyz/fullchain.pem
KEY_FILE=/etc/letsencrypt/live/api.ddhlf.xyz/privkey.pem
LOG_FILE=/home/admin/ai-testing-intelligence/deploy/https_proxy.log
```

启动并设置开机自启：

```bash
systemctl --user daemon-reload
systemctl --user enable --now ai-testing-intelligence-proxy.service
```

日常管理：

```bash
systemctl --user status ai-testing-intelligence-proxy.service
systemctl --user restart ai-testing-intelligence-proxy.service
systemctl --user stop ai-testing-intelligence-proxy.service
```

查看日志：

```bash
journalctl --user -u ai-testing-intelligence-proxy.service -n 200 --no-pager
```

代理自身还会写入：

```text
/home/admin/ai-testing-intelligence/deploy/https_proxy.log
```

确认端口归属：

```bash
sudo ss -ltnp '( sport = :443 or sport = :8080 )'
```

预期：

- `443`：`deploy/https_proxy.py` 对应的 Python 进程
- `127.0.0.1:8080`：Docker Web 端口映射

## 7. HTTPS 证书

当前证书：

```text
/etc/letsencrypt/live/api.ddhlf.xyz/fullchain.pem
/etc/letsencrypt/live/api.ddhlf.xyz/privkey.pem
```

查看证书：

```bash
sudo certbot certificates
```

证书更新后，运行中的 Python 代理不会自动重新加载证书，需要重启：

```bash
systemctl --user restart ai-testing-intelligence-proxy.service
```

### 已知证书续期问题

现有 Certbot 使用 `standalone` 验证方式。执行以下模拟续期：

```bash
sudo certbot renew --dry-run
```

本次返回失败，Let's Encrypt 从公网访问以下地址时收到 HTTP `403`：

```text
http://api.ddhlf.xyz/.well-known/acme-challenge/<token>
```

因此，证书自动续期目前不能视为可靠。后续运维必须在证书到期前处理以下事项：

1. 确认阿里云安全组允许公网 TCP `80`。
2. 确认域名 `api.ddhlf.xyz` 的 A 记录指向 `39.102.75.100`。
3. 检查是否有云 WAF、CDN、域名转发或其他边界规则返回 `403`。
4. 修复后重新运行 `sudo certbot renew --dry-run`，必须看到模拟续期成功。
5. 为 Certbot 增加成功续期后的代理重启 hook，或迁移到 Caddy/Nginx 统一管理 HTTPS。

在续期问题解决前，应定期执行 `sudo certbot certificates` 检查到期时间。

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

# HTTPS 代理服务
systemctl --user is-active ai-testing-intelligence-proxy.service
systemctl --user is-enabled ai-testing-intelligence-proxy.service
```

预期：两个健康检查都返回 `{"status":"ok"}`，代理服务返回 `active` 和 `enabled`。

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
systemctl --user status ai-testing-intelligence-proxy.service
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
2. **证书续期风险**：Certbot `standalone` 模拟续期收到公网 HTTP `403`，必须修复并重新验证。
3. **代理实现限制**：当前 Python 代理用于延续既有部署，长期建议迁移到 Caddy 或 Nginx，以获得更可靠的 TLS、证书续期、访问日志和反向代理能力。
4. **代理权限**：systemd 用户服务通过 `sudo` 启动监听 443 的 Python 进程。后续建议使用 Caddy/Nginx，或为代理配置最小化端口绑定能力，减少 root 进程。
5. **日志轮转**：`deploy/https_proxy.log` 当前未配置 logrotate，需防止日志长期增长占满磁盘。
6. **数据库迁移**：当前应用已使用 Alembic，`migrate` 服务必须以 `Exited (0)` 完成后 API 和 Worker 才能启动。升级前仍须备份并检查迁移脚本。
7. **缓存依赖 Redis**：读路径列表/详情/统计依赖 Redis 缓存。Redis 故障时读路径自动降级直查数据库，功能可用但会变慢；升级或扩容 Redis 时留意 `ATI_REDIS_URL`。
8. **低内存运维**：服务器仅 2 GB 内存。升级后检查 `docker stats` 确认各容器内存未持续触顶；若 Postgres 内存紧张，优先检查长事务和慢查询，不要盲目调大 `shared_buffers`。

## 12. 上线完成判定

只有以下条件全部满足，才视为服务正常上线：

- `docker compose ps -a` 中 API、Worker、Web、PostgreSQL、Redis、MinIO 均运行，`migrate` 为 `Exited (0)`；
- PostgreSQL、Redis 为 `healthy`；
- `curl http://127.0.0.1:8080/api/v1/health` 返回 `{"status":"ok"}`；
- `ai-testing-intelligence-proxy.service` 为 `active`、`enabled`；
- HTTPS 健康接口返回 `{"status":"ok"}`；
- `work-tracker-proxy.service` 为 `inactive`、`disabled`；
- 磁盘有足够可用空间；
- Certbot 模拟续期通过，或已经部署其他可靠的证书自动续期方案；
- Redis 缓存可达：`docker compose exec api python -c "from app.cache import get_json; import asyncio; print(asyncio.run(get_json('health:check')))"` 不报错即可（返回 `None` 正常，Redis 可达且降级未触发即算通过）。
