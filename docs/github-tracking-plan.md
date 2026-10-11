# GitHub 项目持续追踪功能规划

## 当前决策（2026-10-11）

GitHub 追踪调整为**平台主动探索、自动研究、只推荐未曾进入报告的新项目**。本节取代下方历史规划中需要用户订阅确认、只分析被关注项目、反复推荐同一项目或手动生成日报的冲突描述；历史规划保留作为决策记录。具体流程与验收标准见 [GitHub 自动发现方案](github-auto-discovery-plan.md)。

- **自动闭环**：worker 每 24 小时读取关注主题，搜索相关项目并推进分页游标，自动完成去重、确定性评分、深度分析和日报生成。未配置主题时沿用语言发现；用户不必先点击发现或关注。
- **每天最多 5 个**：每份日报最多展示 5 个全新项目，同一业务日期多次触发也不能超过每日 5 个。没有足够的新项目就少报，没有新结果时不拿历史项目凑数。
- **历史条目永久排除**：任何历史 `GitHubReportItem` 都意味着该项目已推荐，后续新项目推荐和新报告必须排除，包含旧报告中分析失败后降级展示的项目。全新指平台从未在报告中推荐过，并非仓库创建时间新。
- **失败可重试**：仅搜索到、被关注、已分析但未发表的项目均不因此排除。分析失败且未入报告的项目可在后续重试；报告和条目成功提交后才算发表，提交回滚不产生历史排除。不新增 `researched` 状态。
- **稳定调度与幂等**：探索运行时间、状态和分页进度独立持久化，不依赖会被其他操作更新的仓库时间。零新项目也记录本轮完成，失败采用有限重试；同一业务日期自动与手动触发共享幂等规则，并发不重复发表。
- **成果优先**：页面先展示最新研究结果，再提供历史报告、关注主题和可选反馈。历史报告、仓库及 Markdown 产物继续保留，可以查阅和导出。
- **分析范围**：复用 `ATI_ANALYSIS_*` 深度分析项目摘要、测试价值、应用场景与落地建议。关注和忽略用于反馈；关注不再是研究的前置条件，忽略项目不参与自动推荐。
- **邮件送达**：已发布的GitHub日报接入每日情报邮件，与内容情报共用北京时间发送时间和收件邮箱管理，GitHub项目不额外套用内容情报的邮件价值门槛。邮件须等待当天采集、内容分析及GitHub研究完成；任一部分有新结果就发送，两部分都为空才跳过，失败或尚未完成时延迟检查。详见[每日情报邮件说明](daily-email-digest-zh.md)。
- **本次边界**：继续使用公开 API 并保留 `ATI_GITHUB_TOKEN` 兼容入口，不新增 PAT 设置流程；支持站内展示、Markdown导出和每日汇总邮件，IM、周报、月报以及已研究项目的持续快照追踪仍不在本次范围。

实现遵循 TDD，重点覆盖历史报告去重、自动主题调度、分页推进、失败未发表重试、空结果、并发和每日上限；不以现有 `intel_status` 或关注状态替代历史报告事实。

## 历史规划（2026-09，冲突内容以当前决策为准）

## 一、需求与目标

在现有「技术情报雷达」基础上新增一条 **GitHub 追踪维度**：持续发现值得关注的开源项目，按日/周/月三种粒度产出筛选、整理、归档后的研究分析报告，帮助用户发现有价值的开源工具、技术趋势与产品机会。

三个核心分析维度（用户明确要求）：
- **技术栈**：语言、框架、基础设施分类
- **应用场景**：赛道归类（AI Agent / 开发工具 / RAG / 推理引擎 / DevSecOps 等）
- **项目组合**：多项目之间的关联与协同（如「Agent 生态」组合）

## 二、同类项目调研结论

| 项目 | 数据源 | 核心机制 | 可借鉴点 |
|---|---|---|---|
| Open Source Radar | GitHub REST API（不抓页面） | 定期快照 → 时间序列可见运动 | 快照机制 + Momentum Score 确定性打分 + 分规模档位（New & Notable / Fast Movers） |
| GitHub Trending Radar | 抓 Trending HTML | profile 打分 →「只看 5 个 + 为什么 + 怎么用」 | collector/report/email 分层解耦；从「25 个全看」到「5 个可行动」，**减法定位** |
| TrendRadar | 多源热榜 | 权重打分（rank/frequency/hotness）+ 多渠道推送 | 打分权重可调、Fork + Actions 免运维 |
| GitHub Trending Tracker | 抓 Trending 页 | 按语言/周期/keyword 过滤 star 动量 | 字段约定：抓取时间/时区/语言/star 增量 |

**共性方法论**：
1. **快照优于单次抓取**——趋势的本质是「增量」，必须存时间序列。
2. **确定性打分 + 个性化 profile 双轨**——先算客观动量（star delta、push 活跃度、规模），再用 profile（关键词/语言/赛道）做个性化过滤。
3. **分层解耦**：collector（拿）→ scorer（判）→ analyzer（析）→ report（报）→ archive（存）。
4. **减法定位**——报告的价值在「省注意力」，不在「信息全」。

## 三、与现有系统的结合点（同构复用）

现有系统已有 `Source → SourceEndpoint → FetchRun → ContentItem → analyzer(LLM)` 完整流水线。GitHub 追踪本质是**新增一种数据源类型 + 快照表 + 报告表 + 三个新分析维度**，最大化复用采集调度、LLM 分析、Markdown 交付、收藏归档。

关键差异：RSS 采集是「新增内容」，GitHub 追踪是「同一批 repo 反复采样记录增量」——因此必须新增 `GitHubSnapshot` 快照表，而非简单复用 `content_items`。

## 四、总体架构

```
发现候选 ──► 用户订阅/确认 ──► 周期快照 ──► 动量打分 ──► LLM 分析 ──► 报告生成 ──► 归档
   │                            │             │             │              │
GitHub Trending /            GitHubRepo   GitHubSnapshot  Momentum       Report
GitHub Search /               (元数据)      (时间序列)      Score        ReportItem
用户手动添加                                                               (日报/周报/月报)
```

## 五、数据模型设计（新增）

- **GitHubRepo**：追踪的仓库元数据
  `id / full_name / description / primary_language / topics(JSON) / homepage / license / archived / stars / forks / pushed_at / created_at / updated_at`
- **GitHubSnapshot**（核心，时间序列）
  `id / repo_id / snapshot_at / stars / forks / open_issues / watchers / star_delta_24h / star_delta_7d / pushed_at / contributors_count / raw(JSON)`
  唯一约束 `(repo_id, snapshot_at)`，支持按日/周/月聚合增量。
- **Report**（报告产物）
  `id / report_type(daily|weekly|monthly) / period_start / period_end / title / summary / body_markdown / status / generated_at`
- **ReportItem**（报告条目，关联 repo/内容）
  `id / report_id / repo_id / rank / momentum_score / highlight / analysis / tags(JSON)`

**复用现有**：`ContentItem` 的 `applicable_scenarios`（应用场景）、`analysis_tags`（技术栈标签）、`adoption_suggestions`、`analysis_risks` 字段；`source_type = "github"` 的新 `Source`。项目组合分析建议新增 `analysis_combinations(JSON)` 字段或独立 `ReportTopic` 表承载。

## 六、采集与快照机制

- **发现候选**：GitHub Trending 页（`?since=daily|weekly|monthly`，按语言过滤）+ GitHub Search API 组合，产出「值得关注」候选列表。
- **订阅确认**：用户在「雷达界面」查看候选，勾选/添加要持续追踪的 repo（贴合已有「信源管理」交互）。
- **快照频率**：日报级 repo 每 24h 采样一次；周报/月报基于快照聚合。沿用现有 worker 定时调度。
- **去重与增量**：repo 以 `full_name` 唯一，快照以 `(repo_id, snapshot_at)` 唯一；star delta 由相邻快照差值计算。

## 七、动量评分（Momentum Score）

确定性组合（类比 Open Source Radar，不依赖 LLM）：
`score = w1 * 近期 star_delta(24h/7d 归一化) + w2 * push 活跃度 + w3 * 规模衰减(避免大 repo 霸榜)`

- 分档：**New & Notable**（<5k star 的早期信号）、**Fast Movers**（增量断层领先）、**Rising**（持续爬升）、**Watch**（平稳观察）。
- 个性化 profile 过滤：用户关注的语言/赛道/关键词（复用 `Source.languages/topics`）。

## 八、LLM 分析维度（复用 analyzer）

对进入报告的 repo 复用 OpenAI 兼容模型，产出结构化 JSON：
- **技术栈**：语言、关键依赖、基础设施层归类
- **应用场景**：映射到 `applicable_scenarios`
- **项目组合**：识别与其他追踪 repo 的协同关系（互补/替代/生态上下游），落到 `analysis_combinations`
- **行动建议 + 风险**：复用 `adoption_suggestions` / `analysis_risks`

## 九、日报 / 周报 / 月报设计

| 粒度 | 定位 | 内容 |
|---|---|---|
| 日报 | 快讯 | 24h/7d star 增量 Top N、「你该看的 5 个 + 为什么 + 怎么用」、单项目速览 |
| 周报 | 趋势复盘 | 本周榜单 + 赛道分布（技术栈/应用场景占比）+ 核心趋势小结 + Fast Movers |
| 月报 | 深度解读/归档 | 技术拐点 + 项目组合分析 + 完整归档 + 下月观察清单 |

报告默认站内展示 + 复用现有 Markdown 导出（收藏/合并导出）；推送渠道（邮件/IM）作为可选二期。

## 十、归档与历史对比

- Report 按月归档，`body_markdown` 落库，支持历史回看。
- 快照时间序列支持「项目成长轨迹」复盘（如某 repo 从 New & Notable 到 Top 的完整曲线）。

## 十一、分阶段实施（TDD）

- **Phase 1（MVP）**：GitHubRepo + GitHubSnapshot 表与迁移 → 采集/快照脚本 + 动量打分 → 日报（站内 + Markdown 导出）。覆盖「持续追踪 + 日报」闭环。
- **Phase 2**：周报/月报 + 技术栈/应用场景/项目组合三维度 LLM 分析 + 归档。
- **Phase 3**：候选发现增强（Search/Trending 融合）+ 个性化 profile 过滤 + 推送渠道（可选）。

每步遵循 TDD：先补 pytest（后端）用例，再写实现，跑通全部测试才交付。

## 十二、已确认的决策（定稿 2026-09）

1. **GitHub 鉴权**：MVP 暂不接 Personal Access Token，以无 token 直连公开 Search/Core API；但**预留**——`Settings` 增加 `github_token` 配置项（`ATI_GITHUB_TOKEN`），采集客户端预留 `Authorization: Bearer` 注入逻辑（token 为空不带、非空则带），后续切换免改业务代码。
2. **发现 vs 订阅**：采用「系统自动发现候选（零配置）+ 一键关注/忽略」，无需用户手动添加 repo（见 `github-auto-discovery-plan.md`）。
3. **报告送达**：MVP 只做站内展示 + Markdown 导出（复用现有 content 导出能力），推送（邮件/IM）**延后**，不实现。
4. **分析深度**：MVP 只做「确定性动量打分（免费，不调 LLM）+ 轻量 LLM 摘要（高置信候选批量一次）」；场景/组合/建议/风险等深度分析延后到 Phase 2。
