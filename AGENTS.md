## 项目概述
AI 测试情报雷达（Signal Atlas / 技术情报雷达）—— 智能测试内容发现、信源管理、无损耗采集、历史去重与 Markdown 交付的全栈系统。信源经 RSS/Web 采集，交由 OpenAI 兼容模型分析生成测试情报（场景/建议/风险），支持收藏与合并导出 Markdown 报告。

## 技术栈
- **前端**：`apps/web` — React 19 + Vite 7 + Ant Design 6 + TypeScript（纯 SPA，无服务端壳）。包管理器 pnpm。
- **后端**：`apps/api` — Python 3.12 + FastAPI + SQLAlchemy(Async) + Alembic，默认 SQLite，生产用 PostgreSQL；通过 Redis 缓存读路径；worker 服务定时采集。
- **编排**：Docker Compose（postgres/redis/minio/api/worker/web），生产部署经 nginx 统一对外。
- 辅助服务：opencage（信源发现可用时经 Vite proxy 至本地 8000）。

## 目录结构
- `apps/web/src/App.tsx` — 前端主入口（单文件 SPA，含登录、情报雷达、信源管理、采集内容、用户管理等视图）
- `apps/api/app/` — FastAPI 应用（`main.py`、`routers/`、`models.py`、`analyzer.py`、`fetcher.py`、`worker.py`）
- `apps/api/migrations/` — Alembic 迁移（SQLite 与 PostgreSQL 共用）
- `scripts/` — 部署/预览包装脚本
- `docs/` — 部署中文指南、上线操作记录、用户手册

## 关键入口 / 核心模块
- 前端 Vite dev server：`apps/web` 下 `pnpm exec vite --host 0.0.0.0 --port <port>`，proxy `/api` → `localhost:8000`
- 后端：`apps/api` 下 `uv run uvicorn app.main:app`，API 统一前缀 `/api/v1`（含 `/health`）
- 数据库迁移：`apps/api` 下 `.venv/bin/alembic upgrade head`
- 预览脚本：`scripts/preview-build.sh` / `scripts/preview-run.sh`
- 部署脚本：`scripts/deploy-build.sh` / `scripts/deploy-run.sh`（部署形态 = FastAPI 单进程全栈：uvicorn 监听 5000，既服务 `/api/v1/*` 又挂载 `apps/web/dist` SPA 静态产物，见 `app/main.py` 尾部 `ATI_WEB_DIST` 分支）

## 运行与预览
预览型项目（`project_type=web`，`preview_enable=enabled`）。`.coze` 的 `[dev]` 指向 preview-build.sh（装依赖+迁移）+ preview-run.sh（起后端 8000 + 前端 5000）。对外暴露 5000，端口从 `.preview` 读取。部署（veFaaS）为 FastAPI 单进程全栈（API + 前端静态同端口 5000）；用户自托管仍走 Docker Compose 全栈。

## 用户偏好与长期约束
- Node 项目只用 pnpm；Python 用 uv + 项目内 `.venv`。
- 后端依赖安装用阿里云 PyPI 镜像（`--index-url https://mirrors.aliyun.com/pypi/simple/`），否则下载极慢。
- 不修改已创建的 `sub_id`（d6048ed2）。

## 常见问题和预防
- 前端无 node_modules：先 `pnpm install`（根目录 `package-lock.json` 存在，用 pnpm 会生成 pnpm-lock）。
- 后端第一次跑：先 `uv venv` + `alembic upgrade head`，否则 API 启动后查询报表找不到表。
- Vite 端口占用会自动 +1（5001），重启预览前先清理 5000 残留。
- 预览需同时起后端(8000)与前端(5000)，否则前端 API 请求失败。
- **部署 run 阶段禁止任何现场下载/安装**（veFaaS 启动超时 30s）：`npx serve` 现场下载包会超时被杀，已改为 uvicorn 毫秒级启动；Python 依赖必须在 build 阶段 `uv pip install --target .python_deps` 打进产物包。此环境 `fuser -k` 可能杀不掉进程，必要时按 `ss -lptn` 的 pid 直接 kill。
- **后端 pydantic settings 带 `env_prefix="ATI_"`**：环境变量名是 `ATI_DATABASE_URL`、`ATI_WEB_DIST` 等；export `DATABASE_URL` **无效**（会静默回落默认 `sqlite:///./development.db` 相对 CWD——alembic 与 uvicorn CWD 不同时会连两个不同的库文件，表现就是 "no such table" + 迁移"成功"但空跑）。deploy-run.sh 统一用绝对路径 `/tmp/signal_atlas.db` + `ATI_` 前缀变量注入。
- **veFaaS 部署环境为 FastAPI 单进程全栈**（`requires = ["nodejs-24", "python-3.12"]`）：登录/信源等 API 与前端页面同一服务，SQLite 存 `/tmp/signal_atlas.db`；FaaS 实例冷启动后 DB 会重置，run 脚本每次幂等执行 alembic 迁移 + `app.bootstrap` 重建 admin（密码取 `ATI_BOOTSTRAP_PASSWORD`，默认 PreviewAdmin!2026）。定时采集 worker 在 FaaS 形态下不运行。
- 沙箱重启后预览前后端进程会全部丢失（5000/8000 无监听），重新执行 `scripts/preview-run.sh` 即可恢复。