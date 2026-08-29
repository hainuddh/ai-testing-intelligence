# AI Testing Intelligence 部署安装手册

本文说明如何在 Linux 服务器上使用 Docker Compose 部署 AI Testing Intelligence。命令默认在项目根目录执行，即 `compose.yaml` 所在目录。

## 1. 部署架构

Docker Compose 会启动以下服务：

| 服务 | 用途 | 对宿主机开放端口 |
| --- | --- | --- |
| `web` | Nginx、前端静态资源和 API 反向代理 | `8080` |
| `api` | FastAPI 应用 | 无 |
| `postgres` | PostgreSQL 17 和 pgvector | 无 |
| `redis` | Redis 8 | 无 |
| `minio` | MinIO 对象存储 | 无 |

浏览器访问 `web`，`web` 将 `/api/` 请求转发给内部的 `api:8000`。PostgreSQL、Redis、MinIO 和 API 默认只在 Compose 内部网络可访问。

## 2. 服务器要求

推荐配置：

- Ubuntu 22.04/24.04 或其他支持 Docker Engine 的 Linux 发行版
- 2 核 CPU
- 4 GB 内存
- 20 GB 以上可用磁盘空间
- Docker Engine 24 或更高版本
- Docker Compose v2
- 可选：域名及指向服务器公网 IP 的 DNS 记录

检查 Docker：

```bash
docker --version
docker compose version
```

如果尚未安装 Docker，请使用 Docker 官方文档提供的安装方式，不要使用来源不明的一键安装脚本：

<https://docs.docker.com/engine/install/>

将部署用户加入 `docker` 用户组后需要重新登录。请注意，该用户组具有接近 root 的权限。

## 3. 获取代码

```bash
git clone https://github.com/hainuddh/ai-testing-intelligence.git
cd ai-testing-intelligence
```

生产部署建议检出明确的版本标签或提交，而不是长期跟随未固定的分支：

```bash
git checkout <版本标签或提交哈希>
```

## 4. 配置环境变量

复制环境变量模板：

```bash
cp .env.example .env
chmod 600 .env
```

生成三个互不相同的随机值：

```bash
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

编辑 `.env`：

```dotenv
POSTGRES_DB=ai_testing_intelligence
POSTGRES_USER=ati
POSTGRES_PASSWORD=<第一个随机值>
MINIO_ROOT_USER=ati-minio
MINIO_ROOT_PASSWORD=<第二个随机值>
ATI_JWT_SECRET=<第三个随机值>
```

注意事项：

- 不要把 `.env` 提交到 Git，也不要通过聊天或工单传递其中的值。
- 每个环境使用独立密钥，开发、测试和生产环境不要共用。
- 当前版本会把 `POSTGRES_PASSWORD` 插入数据库 URL。请使用上述十六进制随机值，避免 `@`、`:`、`/`、`#`、`%` 等 URL 保留字符导致连接失败。
- 修改 `ATI_JWT_SECRET` 会使现有登录令牌全部失效。

验证 Compose 配置能够展开：

```bash
docker compose config --quiet
```

该命令无输出且退出状态为 0 即表示配置有效。不要把不加 `--quiet` 的完整输出复制到公共位置，因为输出会包含密钥。

## 5. 网络和防火墙

内网试运行时可开放 TCP `8080`。公网生产环境建议只开放：

- TCP `22`：SSH，最好限制来源 IP
- TCP `80`：HTTP，用于跳转和证书签发
- TCP `443`：HTTPS

不要向公网开放 PostgreSQL `5432`、Redis `6379`、MinIO `9000/9001` 或 API `8000`。

如果准备通过宿主机反向代理提供 HTTPS，建议将 `compose.yaml` 的 Web 端口限制到回环地址：

```yaml
services:
  web:
    ports:
      - "127.0.0.1:8080:80"
```

修改后可通过 `curl http://127.0.0.1:8080/api/v1/health` 在服务器本机检查服务，但公网无法绕过 HTTPS 直接访问 `8080`。

## 6. 启动服务

构建并后台启动：

```bash
docker compose up -d --build
```

检查容器状态：

```bash
docker compose ps
```

查看启动日志：

```bash
docker compose logs --tail 200 api web postgres
```

需要持续查看日志时：

```bash
docker compose logs -f api web
```

首次启动时，API 会使用 SQLAlchemy `create_all` 创建缺失的数据表。

## 7. 初始化管理员

确认 API 已启动后创建第一个管理员：

```bash
docker compose exec api python -m app.bootstrap admin '<管理员密码>'
```

将 `admin` 替换为实际用户名，并使用独立的高强度密码。当前版本要求密码作为命令行参数，命令可能进入 Shell 历史。执行后应从历史中删除该条记录，且不要在多人可见的终端中操作。

如果用户名已经存在，命令会报告 `User '...' already exists`，不会覆盖原用户。

## 8. 验证部署

检查健康接口：

```bash
curl --fail http://127.0.0.1:8080/api/v1/health
```

预期返回：

```json
{"status":"ok"}
```

检查运行状态：

```bash
docker compose ps
```

然后访问：

```text
http://<服务器地址>:8080
```

如果已经配置 HTTPS，则访问对应的 `https://` 域名。使用刚创建的管理员账号登录，并创建一个测试信源验证读写链路。

## 9. 配置 HTTPS

生产环境必须使用 HTTPS，因为前端会在浏览器 `localStorage` 中保存访问令牌。

以下为 Caddy 示例。先确保域名已解析到服务器，并将 Web 端口绑定到 `127.0.0.1:8080`。

安装 Caddy：

<https://caddyserver.com/docs/install>

在 Caddyfile 中配置：

```caddyfile
intelligence.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8080
}
```

将 `intelligence.example.com` 替换为实际域名，然后检查并重载配置：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

验证：

```bash
curl --fail https://intelligence.example.com/api/v1/health
```

Caddy 会自动申请和续期证书。若使用 Nginx 或云负载均衡器，也应把流量转发到 `127.0.0.1:8080`，并传递 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto` 请求头。

## 10. 日常运维

查看状态：

```bash
docker compose ps
```

查看资源占用：

```bash
docker stats
```

查看最近日志：

```bash
docker compose logs --tail 200
```

重启单个服务：

```bash
docker compose restart api
```

停止服务并保留数据卷：

```bash
docker compose down
```

重新启动：

```bash
docker compose up -d
```

不要在需要保留数据时执行 `docker compose down -v`，因为 `-v` 会删除 Compose 管理的数据卷。

## 11. 更新应用

更新前先备份数据库，并阅读目标版本的发布说明。当前项目尚未提供数据库迁移工具，因此涉及模型或表结构变化的版本不能仅依靠重新构建完成升级。

不涉及数据库结构变化时：

```bash
git fetch --all --tags
git checkout <目标版本标签或提交哈希>
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail 200 api web
```

更新后重新执行健康检查和登录、查询、创建信源等冒烟测试。

## 12. 数据备份

### 12.1 PostgreSQL 备份

创建备份目录：

```bash
mkdir -p backups
chmod 700 backups
```

生成自包含的 PostgreSQL 自定义格式备份：

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/postgres.dump
```

确认文件存在且不为空：

```bash
test -s backups/postgres.dump
```

备份文件包含业务数据，应加密后存储到服务器之外，并设置保留周期。仅有备份文件不够，必须定期在隔离环境演练恢复。

### 12.2 PostgreSQL 恢复

恢复会覆盖目标数据库中的对象和数据。先停止 API，避免恢复期间产生写入：

```bash
docker compose stop api
```

恢复指定备份：

```bash
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < backups/postgres.dump
```

恢复完成后启动 API 并验证：

```bash
docker compose start api
curl --fail http://127.0.0.1:8080/api/v1/health
```

如果恢复失败，不要直接删除数据卷；先保留日志和备份并确认数据库状态。

### 12.3 Redis 和 MinIO

当前已配置 Redis AOF 和 MinIO 数据卷，但应用现有功能尚未展示对二者的业务读写。正式启用相关功能后，应分别制定一致性备份和恢复方案，不能只复制正在写入的数据卷目录。

## 13. 故障排查

### Compose 提示缺少变量

错误示例：

```text
required variable POSTGRES_PASSWORD is missing a value
```

确认项目根目录存在 `.env`，变量名称与 `.env.example` 一致，然后执行：

```bash
docker compose config --quiet
```

### API 无法连接 PostgreSQL

检查容器和日志：

```bash
docker compose ps
docker compose logs --tail 200 postgres api
```

如果密码包含 URL 保留字符，改用十六进制随机密码后需要谨慎处理已经初始化的数据卷；修改 `.env` 不会自动修改 PostgreSQL 数据卷中现有用户的密码。

### 页面可打开但 API 请求失败

检查健康接口和 Web 代理日志：

```bash
curl -v http://127.0.0.1:8080/api/v1/health
docker compose logs --tail 200 web api
```

确认 `api` 服务正在运行，并且没有单独修改 Compose 服务名。`apps/web/nginx.conf` 当前固定代理到 `api:8000`。

### 容器反复重启

```bash
docker compose ps
docker compose logs --tail 300 <服务名>
docker inspect <容器名>
```

重点检查环境变量、磁盘空间、内存不足和数据卷权限。

### 端口 8080 被占用

修改 `compose.yaml` 中 Web 端口映射，例如：

```yaml
ports:
  - "127.0.0.1:18080:80"
```

同时把反向代理上游和健康检查地址改为 `127.0.0.1:18080`。

## 14. 当前版本限制

生产上线前应评估以下限制：

- 数据库只通过 `create_all` 初始化，尚无 Alembic 等迁移机制。
- 管理员初始化密码通过命令行参数传递。
- 登录接口尚无应用级限流；公网部署应至少在边界代理或 WAF 增加限流。
- 非 Compose 启动存在开发用 JWT 默认值；生产环境必须显式设置 `ATI_JWT_SECRET`。
- 数据库密码直接进入连接 URL，应使用不含 URL 保留字符的十六进制随机值。
- 当前 Compose 未定义 CPU、内存限制、集中日志、指标采集和告警。
- 应建立补丁更新、离线备份、恢复演练和密钥轮换流程后再承载重要数据。

## 15. 卸载

停止并删除容器和网络，但保留数据卷：

```bash
docker compose down
```

永久删除容器、网络和数据卷：

```bash
docker compose down -v
```

第二条命令会永久删除 PostgreSQL、Redis 和 MinIO 数据。只有在已经验证备份并明确不再需要数据时才能执行。
