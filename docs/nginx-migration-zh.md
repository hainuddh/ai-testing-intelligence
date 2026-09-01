# Nginx HTTPS 代理迁移手册

本文说明如何在 2 GB 内存服务器上，将宿主机的 Python HTTPS 代理迁移为低内存 Nginx。迁移不会改变 Docker Compose 内部结构，`web` 容器仍监听 `127.0.0.1:8080`，PostgreSQL、Redis、MinIO 和 API 端口仍不向公网开放。

## 1. 目标架构

```text
浏览器 HTTPS :443
  -> 宿主机 Nginx（1 个 Worker）
  -> 127.0.0.1:8080
  -> Docker Web Nginx
  -> FastAPI / PostgreSQL / Redis
```

Nginx 是替换 Python 代理，而不是与它长期并行运行。迁移成功后，旧的 `ai-testing-intelligence-proxy.service` 会被停止并禁用。

## 2. 资源约束

迁移脚本使用以下保守配置：

- `worker_processes 1`；
- `worker_connections 512`；
- 到 Docker Web 的空闲保活连接最多 8 条；
- SSL Session Cache 仅 1 MB；
- 代理缓冲区合计保持在较小范围；
- 访问日志使用 16 KB 缓冲并每分钟落盘，错误日志只记录 `warn` 及以上；
- 登录接口按来源 IP 限制为每分钟 5 次，限流区与连接限制区合计 2 MB；
- 不安装推荐包和额外 Nginx 模块；
- 不启用响应缓存、Lua、ModSecurity 等额外功能。

低并发时，Nginx Master 加 1 个 Worker 通常只需要十几 MB 常驻内存。旧 Python 代理停止后，总常驻内存一般不会增加。最终应以服务器上的 `ps`、`free` 和 `docker stats` 实测为准。

## 3. 前置条件

执行前确认：

- 操作系统为 Debian/Ubuntu，使用 `apt-get`；
- 当前部署用户可以执行 `sudo`；
- Compose 应用已经运行；
- `http://127.0.0.1:8080/api/v1/health` 正常；
- 现有证书位于 `/etc/letsencrypt/live/<域名>/`；
- 域名仍指向当前服务器；
- 安全组和防火墙允许公网 TCP 80、443；
- 项目目录中没有未备份的重要手工配置。

不要使用 `sudo` 运行整个脚本。脚本只对安装软件、写入 `/etc` 和管理系统 Nginx 的步骤单独调用 `sudo`，避免在项目目录产生 root 所有者文件。

## 4. 一键迁移

在服务器项目根目录执行：

```bash
chmod +x scripts/migrate-to-nginx.sh
./scripts/migrate-to-nginx.sh
```

默认参数对应当前生产环境：

```text
项目目录：/home/admin/ai-testing-intelligence
域名：api.ddhlf.xyz
本地上游：http://127.0.0.1:8080
证书目录：/etc/letsencrypt/live/api.ddhlf.xyz
```

如部署路径或域名不同，通过环境变量覆盖：

```bash
ATI_PROJECT_DIR=/srv/ai-testing-intelligence \
ATI_DOMAIN=intelligence.example.com \
./scripts/migrate-to-nginx.sh
```

网络受限且 APT 索引已经更新时，可以跳过 `apt-get update`：

```bash
ATI_SKIP_APT_UPDATE=1 ./scripts/migrate-to-nginx.sh
```

暂时不迁移 Certbot 续期方式时：

```bash
ATI_SKIP_CERTBOT_RECONFIGURE=1 ./scripts/migrate-to-nginx.sh
```

## 5. 脚本执行步骤

脚本按以下顺序执行：

1. 校验项目目录、域名、证书、私钥、命令和本地健康接口。
2. 记录 Nginx和旧 Python 代理迁移前的运行、启用状态。
3. 通过 `apt-get install --no-install-recommends nginx` 安装最小化 Nginx。
4. 将原 Nginx 主配置、站点配置和 Certbot 续期配置备份到 `backups/nginx-migration/<时间>/`。
5. 将 Nginx 收敛为 1 个 Worker 和 512 个连接。
6. 写入 HTTP ACME Challenge、HTTPS证书和到 `127.0.0.1:8080` 的代理配置。
7. 运行 `nginx -t`，配置无效时不会切换流量。
8. 停止旧 Python 代理，启动 Nginx，并用 `curl --resolve` 强制访问本机 443，避免 DNS、CDN 或 WAF 造成误判。
9. HTTPS 检查通过后，禁用旧 Python 代理。
10. 如果已安装 Certbot，将续期方式改为 Webroot，安装续期后的 Nginx Reload Hook，并执行模拟续期；失败时回滚，除非显式设置跳过变量。
11. 输出 Nginx 状态、监听端口和进程 RSS，便于核对内存。

在步骤 8 健康检查通过前发生错误，脚本会恢复原 Nginx 配置，并按迁移前状态重新启用和启动 Python 代理。

## 6. 迁移后验证

```bash
sudo nginx -t
sudo systemctl is-active nginx
sudo systemctl is-enabled nginx
systemctl --user is-active ai-testing-intelligence-proxy.service
systemctl --user is-enabled ai-testing-intelligence-proxy.service
curl --fail http://127.0.0.1:8080/api/v1/health
curl --fail https://api.ddhlf.xyz/api/v1/health
sudo ss -ltnp '( sport = :80 or sport = :443 or sport = :8080 )'
ps -C nginx -o pid,rss,cmd
free -h
docker stats --no-stream
```

预期结果：

- Nginx 为 `active`、`enabled`；
- Python 代理为 `inactive`、`disabled`；
- 80、443 由 Nginx 监听；
- 8080 仍只绑定 `127.0.0.1`；
- 本地和公网健康接口都返回 `{"status":"ok"}`；
- 登录后 `/api/v1/auth/me` 不再出现连续 30 秒等待；
- 系统没有持续使用大量 Swap。

## 7. 后续运维

`scripts/ops.sh` 会通过迁移成功后创建的 `/etc/ai-testing-intelligence/nginx-proxy.enabled` 标记自动识别迁移状态。迁移后，以下命令会管理 Nginx，不会重新启动 Python 代理：

```bash
./scripts/ops.sh proxy-start
./scripts/ops.sh proxy-stop
./scripts/ops.sh proxy-restart
./scripts/ops.sh logs-tail proxy
./scripts/ops.sh config-check
./scripts/ops.sh deploy
./scripts/ops.sh upgrade main
```

证书续期验证：

```bash
sudo certbot renew --cert-name api.ddhlf.xyz --dry-run
```

如果模拟续期仍收到 HTTP 403，应检查阿里云安全组、TCP 80、DNS、WAF/CDN 和域名转发。Nginx 迁移本身不能绕过云端返回的 403。

## 8. 手工回退

仅在 Nginx 已确认故障时执行：

```bash
sudo systemctl disable --now nginx
sudo rm -f /etc/ai-testing-intelligence/nginx-proxy.enabled
systemctl --user enable --now ai-testing-intelligence-proxy.service
curl --fail https://api.ddhlf.xyz/api/v1/health
```

迁移脚本创建的备份目录中保存了迁移前的 `nginx.conf`、站点配置、Certbot 续期配置和原有 Deploy Hook。如需长期回退到 Python 代理，还必须从对应时间戳目录恢复 `/etc/letsencrypt/renewal/<域名>.conf`，因为停止 Nginx 后 Webroot 续期无法监听 80 端口。应审查备份后逐文件恢复，不要直接删除整个 `/etc/nginx` 或 `/etc/letsencrypt`。
