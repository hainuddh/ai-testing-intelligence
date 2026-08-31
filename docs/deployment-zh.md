# AI Testing Intelligence 部署安装手册

本文说明如何在 Linux 服务器上使用 Docker Compose 部署 AI Testing Intelligence。命令默认在项目根目录执行，即 `compose.yaml` 所在目录。

## 1. 部署架构

Docker Compose 会启动以下服务：

| 服务 | 用途 | 对宿主机开放端口 |
| --- | --- | --- |
| `web` | Nginx、前端静态资源和 API 反向代理 | `8080` |
| `api` | FastAPI 应用（异步读路径 + Redis 缓存） | 无 |
| `worker` | 定时采集 RSS/Atom 和网页内容，分析后失效缓存 | 无 |
| `migrate` | 启动前执行一次数据库迁移 | 无 |
| `postgres` | PostgreSQL 17 和 pgvector | 无 |
| `redis` | Redis 8，缓存列表查询结果和内容版本 | 无 |
| `minio` | MinIO 对象存储 | 无 |

浏览器访问 `web`，`web` 将 `/api/` 请求转发给内部的 `api:8000`。PostgreSQL、Redis、MinIO 和 API 默认只在 Compose 内部网络可访问。

## 2. 服务器要求

推荐配置：

- Ubuntu 22.04/24.04 或其他支持 Docker Engine 的 Linux 发行版
- 2 核 CPU
- 4 GB 内存（最低 2 GB，并建议配置适当 swap）
- 20 GB 以上可用磁盘空间
- Docker Engine 24 或更高版本
- Docker Compose v2
- 可选：域名及指向服务器公网 IP 的 DNS 记录

针对 2 GB 内存的低配服务器，`compose.yaml` 已包含以下优化：

- PostgreSQL 配置 `shared_buffers=256MB`、`work_mem=4MB`、`max_connections=30`，降低每连接内存占用；
- Redis 限制 `--maxmemory 128mb --maxmemory-policy allkeys-lru`，防止缓存无限膨胀；
- 各容器设置 `mem_limit`，避免单个服务耗尽整机内存触发 swap 抖动；
- API 数据库连接池收敛到 `pool_size=3, max_overflow=3`，Worker 使用 `1,1`；
- 读路径异步化（`async def` + `AsyncSession`），列表查询延迟加载 `body` 大字段，并通过 Redis 缓存列表、详情和统计结果。

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

`.env.example` 还包含以下可选性能与缓存配置，默认值适用于 2 GB 内存服务器，通常无需修改：

```dotenv
# Redis 缓存地址（Compose 内置，默认指向 redis 服务）
ATI_REDIS_URL=redis://redis:6379/0
# 内容情报/采集管理列表与详情缓存时长（秒）
ATI_CONTENT_CACHE_TTL=30
# 数据库状态统计缓存时长（秒）
ATI_DATABASE_CACHE_TTL=60
# 信源列表缓存时长（秒）
ATI_SOURCES_CACHE_TTL=30
# API 数据库连接池大小（低内存服务器建议 3 以内）
ATI_DB_POOL_SIZE=3
# API 数据库连接池溢出上限
ATI_DB_MAX_OVERFLOW=3
```

`ATI_REDIS_URL` 为空时，缓存层自动降级为直查数据库，功能不受影响，但会失去缓存加速能力。若在 Compose 之外手动运行 API，需将 `ATI_REDIS_URL` 指向实际的 Redis 实例。

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
docker compose logs --tail 200 migrate api worker web postgres
```

需要持续查看日志时：

```bash
docker compose logs -f api worker web
```

首次启动和升级时，`migrate` 服务会先执行 Alembic 数据库迁移；迁移成功后 API 和 Worker 才会启动。

## 7. 初始化管理员

确认 API 已启动后创建第一个管理员：

```bash
docker compose exec api python -m app.bootstrap admin '<管理员密码>'
```

将 `admin` 替换为实际用户名，并使用独立的高强度密码。当前版本要求密码作为命令行参数，命令可能进入 Shell 历史。执行后应从历史中删除该条记录，且不要在多人可见的终端中操作。

如果用户名已经存在，命令会报告 `User '...' already exists`，不会覆盖原用户。

### 7.1 添加 AI 资讯示例信源

可选执行以下命令，添加一组真实且可重复初始化的 AI 资讯订阅源：

```bash
docker compose exec api python -m app.seed_sources --username admin
docker compose restart worker
docker compose logs -f worker
```

默认包含 OpenAI News、Google AI、Google DeepMind、MIT Technology Review AI 和
Machine Learning Mastery。命令只创建缺失的信源和 RSS/Atom 端点，重复执行不会
重复添加，也不会插入伪造文章。Worker 完成首次采集后，真实订阅内容会显示在
“内容情报”页面。若管理员用户名不是 `admin`，请替换 `--username` 的值。

需要重新初始化内置示例信源时执行：

```bash
docker compose exec api python -m app.seed_sources --username admin --reset
docker compose restart worker
docker compose logs -f worker
```

`--reset` 只会删除并重建上述 5 个内置示例信源，同时清除它们关联的端点、采集运行和
已采集内容。手工创建的信源、用户及其数据不会受到影响。

### 7.2 配置测试情报分析模型

系统需要一个支持 OpenAI Chat Completions 协议的模型，将采集到的通用技术文章转化为
测试技术情报。在 `.env` 中配置：

```dotenv
ATI_ANALYSIS_API_BASE_URL=https://api.openai.com/v1
ATI_ANALYSIS_API_KEY=<模型服务密钥>
ATI_ANALYSIS_MODEL=gpt-4o-mini
```

也可以填写兼容该协议的内部模型网关地址和模型名称。模型地址必须使用 HTTPS，避免文章
内容和 API 密钥通过明文传输。修改后重建并重启 Worker：

```bash
docker compose up -d --build worker
docker compose logs -f worker
```

Worker 会读取新采集和历史待分析内容，生成测试相关性、测试价值、情报摘要、适用测试场景、
落地建议和风险。相关性低于默认阈值 60 的通用 AI 热点会保留在数据库中，但不会进入主雷达。
未配置模型时，内容保持 `pending`，主雷达有意不展示未经分析的通用资讯。模型密钥不得提交
到 Git，也不要写入日志或文档。

默认只把 RSS/Atom 提供的标题和摘要发送给模型服务，不额外下载并发送文章全文。如果明确
接受相应的数据传输、版权和模型调用成本风险，可设置 `ATI_ANALYSIS_FETCH_FULL_CONTENT=true`。
建议优先使用企业内部模型网关或具有明确数据保留政策的模型服务，并限制密钥额度。系统还会
先通过测试、质量、评测、可靠性、安全、缺陷、回归等确定性信号筛选候选文章，再调用模型，
降低通用热点、提示注入和无效调用进入主雷达的概率。

### 7.3 导出 Markdown 测试情报报告

在“内容情报”页面勾选一张或多张卡片，点击“导出 Markdown”。系统会按选择顺序合并为一份
排版完整的 `.md` 报告，包括标题、来源、评分、情报摘要、测试价值分析、适用测试场景、
场景落地建议、风险、标签和原文证据链接。可使用“全选当前页”批量选择，跨分页选择会保留；
点击“清空选择”可重置。只有通过测试相关性门控的已分析情报允许导出。

### 7.4 管理自动采集内容

管理员可进入“采集管理”查看 PostgreSQL 中保存的全部采集记录，包括：

- `pending`：已采集，等待分析
- `analyzed`：已分析并进入主雷达
- `filtered`：已分析但测试相关性不足
- `failed`：模型调用或结果解析失败

管理页面支持按标题/摘要关键词、分析状态、信源和采集日期范围组合查询，并支持分页、单条删除、
全选当前页和批量删除。删除操作会清除该条原始采集内容及其分析结果，但不会删除信源或采集端点；
后续如果订阅源再次提供相同 URL，采集器可以重新写入。删除会等待正在进行的采集和分析批次完成，
避免与 Worker 并发写入冲突。该页面和相关 API 仅管理员可访问。

由于删除信源会级联删除该信源的全部采集内容，信源删除同样仅管理员可执行；维护者仍可新增、
编辑信源以及管理采集端点。删除采集端点前界面会要求确认，并同时清除该端点的运行历史。

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

更新前先备份数据库，并阅读目标版本的发布说明。项目已使用 Alembic 版本化迁移，升级时会由 `migrate` 服务自动执行迁移；但涉及表结构或索引变化的版本，仍应在执行前人工审查迁移脚本，必要时先在隔离环境演练。

常规升级步骤：

```bash
git fetch --all --tags
git checkout <目标版本标签或提交哈希>
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail 200 migrate api worker web
```

更新后重新执行健康检查，并确认 `migrate` 服务为 `Exited (0)`。若本次升级引入了缓存相关改动，建议在升级后执行 `docker compose restart api worker`，让缓存版本号与代码保持一致。之后做登录、查询、创建信源等冒烟测试。

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

恢复会覆盖目标数据库中的对象和数据。先停止 API 和 Worker，避免恢复期间产生写入：

```bash
docker compose stop api worker
```

恢复指定备份：

```bash
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < backups/postgres.dump
```

恢复完成后启动 API 并验证：

```bash
docker compose start api worker
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

- 数据库使用 Alembic 执行版本化迁移；升级前仍应备份并检查目标版本说明。
- 管理员初始化密码通过命令行参数传递。
- 登录接口尚无应用级限流；公网部署应至少在边界代理或 WAF 增加限流。
- 非 Compose 启动存在开发用 JWT 默认值；生产环境必须显式设置 `ATI_JWT_SECRET`。
- 数据库密码直接进入连接 URL，应使用不含 URL 保留字符的十六进制随机值。
- 缓存依赖 Redis；若 Redis 故障，读路径会自动降级直查数据库，但会失去缓存加速。
- 内容列表缓存通过 Worker 采集/分析后主动失效，最长滞后一个 Worker 轮询周期（默认 60 秒）加网络往返。
- 登录、写入类操作（信源/用户/采集删除）不缓存，保持强一致。
- 已为各服务设置内存上限并收敛连接池，但尚未提供集中日志、指标采集和告警。
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
