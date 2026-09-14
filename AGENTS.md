## 项目概述
AI 测试情报雷达（Signal Atlas / 技术情报雷达）—— 智能测试内容发现、信源管理、无损耗采集、历史去重与 Markdown 交付的全栈系统。信源经 RSS/Web 采集，交由 OpenAI 兼容模型分析生成测试情报（场景/建议/风险），支持收藏与合并导出 Markdown 报告。另含 GitHub 项目追踪维度（自动发现候选 + 确定性动量打分 + 日/周/月报，见 `docs/github-tracking-plan.md`）。

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
- 部署脚本：`scripts/deploy-build.sh` / `scripts/deploy-run.sh`（run 阶段用零依赖 Node 静态服务器 `scripts/static-server.mjs` 服务 `apps/web/dist`，支持 SPA fallback）

## 运行与预览
预览型项目（`project_type=web`，`preview_enable=enabled`）。`.coze` 的 `[dev]` 指向 preview-build.sh（装依赖+迁移）+ preview-run.sh（起后端 8000 + 前端 5000）。对外暴露 5000，端口从 `.preview` 读取。生产为 Docker Compose 自托管全栈。

## 用户偏好与长期约束
- Node 项目只用 pnpm；Python 用 uv + 项目内 `.venv`。
- 后端依赖安装用阿里云 PyPI 镜像（`--index-url https://mirrors.aliyun.com/pypi/simple/`），否则下载极慢。
- 不修改已创建的 `sub_id`（d6048ed2）。
- **记账界定（长期约定）**：为在 coze 沙箱环境预览/部署而做的改动（`.coze`、`.preview`、`.gitignore`、`scripts/*`、`AGENTS.md` 中部署/预览相关内容）**不计入"代码修改"**；后续核对/统计代码变更量时予以排除。业务代码仅指 `apps/web/src/**`、`apps/api/app/**`、`apps/api/migrations/**`、`docs/**` 等产品源码与文档。此项仅作记账口径，不做任何 git 回滚/移除跟踪动作。
- **TDD 开发模式（长期约束）**：所有开发遵循 TDD——先写失败测试（Red）→ 写最小实现使其通过（Green）→ 重构（Refactor）。前端用 vitest（`apps/web` 下 `pnpm exec vitest`，用例在 `apps/web/src/App.test.tsx`）；后端用 pytest（`apps/api` 下 `.venv/bin/pytest`，用例在 `apps/api/tests/`）。任何功能改动需先补写/调整对应测试用例，再写实现，最后跑测试确认全部通过才能视为完成。
- **GitHub 追踪功能（长期约定）**：MVP 三大决策——① 鉴权暂不接 PAT，`ATI_GITHUB_TOKEN`（`Settings.github_token`）预留，采集客户端 token 为空不带 Bearer、非空则带，切换免改业务代码；② 报告只做站内展示 + Markdown 导出（复用现有 content 导出），推送（邮件/IM）延后；③ 分析先「确定性动量打分（纯函数，不调 LLM）+ 轻量 LLM 摘要（高置信候选批量一次）」，深度分析（场景/组合/建议/风险）延后 Phase 2。自动发现走 `Search API`（无 token 10 req/min 独立桶），持续快照走 `Core API`（无 token 60 req/h，规模化需 token）。详见 `docs/github-tracking-plan.md`、`docs/github-auto-discovery-plan.md`。

## 常见问题和预防
- 前端无 node_modules：先 `pnpm install`（根目录 `package-lock.json` 存在，用 pnpm 会生成 pnpm-lock）。
- 后端第一次跑：先 `uv venv` + `alembic upgrade head`，否则 API 启动后查询报表找不到表。
- Vite 端口占用会自动 +1（5001），重启预览前先清理 5000 残留。
- 预览需同时起后端(8000)与前端(5000)，否则前端 API 请求失败。
- 部署 run 阶段禁止用 `npx serve` 之类需现场下载的命令：veFaaS 启动超时 30s，`npx serve` 下载包会超时被杀（已改用零依赖 `scripts/static-server.mjs`）。此环境 `fuser -k` 可能杀不掉进程，必要时按 `ss -lptn` 的 pid 直接 kill。
- **veFaaS 部署面只有前端静态产物，没有后端 API**：`static-server.mjs` 对 `/api/*` 返回 502 JSON（明确提示），不走 SPA fallback——否则 index.html 冒充 JSON 会造成前端 `Unexpected token '<', "<!DOCTYPE "...` 报错。登录等完整功能只在预览环境（预览进程齐活）或 Docker Compose 自托管下可用。
- 沙箱重启后预览前后端进程会全部丢失（5000/8000 无监听），重新执行 `scripts/preview-run.sh` 即可恢复。