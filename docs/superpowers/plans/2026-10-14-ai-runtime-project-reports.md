# 时钟、AI 队列与共享项目研判实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以准确北京时间、受控并发和持久队列生成管理员／当前负责人共享的项目研判。

**Architecture:** 时钟先于初始化及业务期限判断；独立 provider 层处理网络及 JSON；SQLite jobs 原子领取，项目报告按当前负责人版本发布。复用 A 的负责人版本与安全通知接口。

**Tech Stack:** Python 3.12、FastAPI、SQLite、HTTPX、pytest、原生前端。

**Spec:** [工作流设计第三、四节](../specs/2026-10-14-direct-ai-workflows-design.md)、[北京时间设计](../specs/2026-10-14-beijing-clock-design.md)、[全局约束](2026-10-14-direct-ai-workflows.md)。

## Global Constraints

- 总计划全部约束适用；A1 接口及持久 owner_revision 已存在。
- 项目报告不迁移私人 topic_analyses；模型输入不含私人备注和企业名录联系人。
- 新网络规则不读取凭据做探测；授时使用固定 HTTPS 白名单、TLS 校验、无跳转。

## Review Focus

- 宿主时钟差六小时不能污染业务时间；B1 test_clock_ignores_host_offset。
- 同步无一致样本／恢复休眠／六小时失联时，写与调度按时钟设计处理；B1 边界测试。
- 多个入口共用 provider 时不可超并发、后台不能占满前台；B2 调度测试。
- 转交 A→B→A 后旧任务不可发布，且旧负责人不能读任务结果；B3 版本／权限测试。
- 缺正文、截断、服务商 429 或认证失败不制造事实，也不删除旧报告；B2/B3 错误测试。

## B1：准确北京时间入口

**Files:** 新增 `app/clock.py`、`tests/test_clock.py`；修改 `db.py`、`analysis.py`、`subscriptions.py`、`accounts.py`、`main.py`、`workbench.py`、`collectors.py`、`monitoring.py`、`static/app.js`。新增测试时间夹具 `tests/conftest.py`：普通离线测试注入测试锚点；clock 单元测试单独控制样本，不被这个夹具替换；不提供生产绕过开关。

**Interfaces:** `clock.initialize() -> None`、`clock.utc_now() -> datetime`、`clock.now() -> datetime`（+08:00）、`clock.today() -> date`、`clock.status() -> dict`、`clock.shutdown() -> None`；`db.now()` 继续返回 ISO 字符串；公共 `GET /api/clock` 返回可信时间及状态。

- [ ] 写样本一致性、Age/RTT、首同步 8 秒预算、15 分钟同步、六小时退化、时间回退、休眠及宿主偏差测试；期望 UTC 独立于宿主而 +08 表示准确，同日期／期限边界符合设计。
- [ ] 运行新测试，确认无实现时失败；以 mock HTTPS 样本验证，不依赖互联网授时。
- [ ] 实现规格中的三个固定来源、至少两个一致样本及 monotonic 锚点；初始化先于 db.initialize，会话和调度共用时钟。解析源事实仍保留 datetime 的解析能力；只替换墙钟读取，不替换网络预算及登录限流的 monotonic。
- [ ] 六小时无校验时暂停新业务写及任务；只读路径的 refresh_expired 不越过这个限制。一次重新核验派生评分／期限，不整体平移旧记录；前端以服务器时间加 performance.now 展示。
- [ ] 新时间测试及相关期限／会话／订阅／采集测试通过，核验固定公开时间入口，记录本阶段本地提交。

## B2：统一 provider 与持久任务

**Files:** 新增 `app/ai_provider.py`、`app/ai_jobs.py`、`tests/test_ai_provider.py`、`tests/test_ai_jobs.py`；修改 `ai.py`、`assist.py`、`model_config.py`、`management.py`、`main.py` 及模型配置 UI。

**Interfaces:** `ai_provider.complete_json(messages: list[dict], schema: dict, *, task_kind: str, config: dict, budget: int, deadline: float) -> dict` 返回 `data/usage/finish_reason/elapsed_seconds`；`ai_jobs.enqueue(spec: dict, connection=None) -> dict`；`register_handler(kind: str, handler) -> None`；`get(job_id: int, user: dict) -> dict`；`start() -> None`／`stop() -> None`。JobSpec 字段固定 `kind,requested_by_user_id,context_user_id,visibility,opportunity_id,subscription_id,owner_revision,input_hash,priority,payload`。

- [ ] 测试断言最多 2 并发且后台最多 1、前台优先／老任务提权、重复 input_hash 复用任务、重启恢复租约、私有任务隔离及项目任务实时授权；MockTransport 验证 DeepSeek thinking disabled、4096→8192 截断重试、最多 3 次、120 秒总时限／2 MiB、429 退避及认证等待配置。
- [ ] 运行新测试确认失败，再建 ai_jobs 表和唯一 dedupe_key；短事务领取及释放，HTTP 不持有写事务，租约 180 秒，15 秒恢复检查，job 状态与版本 CAS。
- [ ] 接入统一 provider 并移除生成期间的全局拒绝锁；配置保存只短暂保护快照。官方 DeepSeek 使用确认参数，未知兼容服务不注入 thinking。并发设 1–8、默认 2，降低上限时让在途完成后再领取。
- [ ] `ai.explain/assist.generate` 保持为 worker 可调用的纯生成入口，不在内部重复入队；生产 HTTP／调度入口全部通过 jobs 控制并发。对外 POST 只接受白名单请求，任务申请者、业务身份、visibility 和权限由服务端生成，不允许客户端伪造 JobSpec。
- [ ] 暴露 `POST /api/ai-jobs`、`GET /api/ai-jobs/{id}`、`POST /api/ai-jobs/{id}/retry`；旧同步辅助／解读走有限等待兼容，新前端 2 秒轮询。注册 handler 必须在 start 前完成，public_analysis 不等待生成才推送。
- [ ] 新测试与 `tests/test_ai.py tests/test_assist.py tests/test_api.py` 通过，安全日志不含密钥／私人输入；记录本阶段本地提交。

## B3：项目报告、自动触发与授权展示

**Files:** 新增 `app/project_analysis.py`、`tests/test_project_analysis.py`；修改 `team_followups.py`、`accounts.py`、`subscriptions.py`、`main.py`、`static/app.js`、`collaboration.js`、`interactions.js`、`team.js`。

**Interfaces:** `project_analysis.can_use(user: dict, opportunity_id: int, connection=None) -> bool`；`enqueue_if_active(connection, opportunity_id: int, actor_id: int|None) -> dict|None`；`input_version(opportunity_id: int) -> dict`；`get(opportunity_id: int, user: dict) -> dict`；handler `run_project_analysis(job: dict) -> dict`，注册 kind `project_analysis`。复用 A 的 advance_owner_revision、record_event 及 B2 enqueue/get。

- [ ] 测试管理员与当前负责人共享、非负责人及旧负责人 403、普通私人跟进不触发、指派使用接手人上下文、A→B→A 拒旧结果、相同版本跨管理员／员工去重、旧私有解读不共享；报告缺正文／无法验证证据时标未知，私人备注不进入请求。
- [ ] 运行新测试确认失败；建 project_analyses 按公告键存报告、各 hash、owner_revision、report_generation 和任务信息。原文证据 hash 不含分数／last_seen；结构化报告中的事实引用验证后才展示，不改原字段和阶段。
- [ ] 在领取／指派／负责人阶段变更事务 enqueue；启动后的限批补齐及证据变更处理只扫合法活跃项目。负责人转交使旧任务失效，新负责人业务视角重新生成；无负责人仅管理员可使用跟进 AI。
- [ ] 接 `GET /api/opportunities/{id}/project-analysis`、`POST .../project-analysis`；所有读／重试／自动写回检查当前权利及版本，三个入口和团队看板使用同一项目报告。跟进智能填写也后端限管理员／当前负责人；已保存备注保持本人。
- [ ] 有效报告完成／最终失败在提交后 record_event；失效任务不通知。相关新测试及旧 followup/team/AI 测试通过，界面失败保留旧版报告，记录本阶段本地提交。
