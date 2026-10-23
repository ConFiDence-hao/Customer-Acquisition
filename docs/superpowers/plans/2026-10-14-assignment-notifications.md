# 派发提醒与负责人版本实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理员直接指派后，员工我的跟进出现未查看红圈与管理员派发标记，管理员能看到员工领取后的负责人。

**Architecture:** 负责人持久版本与站内通知独立于订阅推送；同一事务记录职责变动及通知。界面轮询只更新徽标和只读区域，不重建正在编辑的表单。

**Tech Stack:** FastAPI、SQLite、原生 JavaScript/CSS、pytest、CUA。

**Spec:** [工作流设计第五节及验收](../specs/2026-10-14-direct-ai-workflows-design.md)、[全局实施约束](2026-10-14-direct-ai-workflows.md)。

## Global Constraints

- 执行总计划全部全局约束；本阶段不生成项目 AI，也不改变订阅推送。
- 查看状态仅本人可读／改；管理员团队接口不能返回员工是否查看。
- 历史负责人来源标记 legacy，不猜测管理员派发，也不补发历史通知。

## Review Focus

- 放弃后再领取仍递增版本，防旧任务复活；A1 test_revision_survives_release。
- A→B→A 的旧查看确认不得消掉新红圈；A1 test_stale_assignment_ack。
- 重复指派同一人不重置已读或加数；A1 test_repeat_assignment_is_idempotent。
- 列表分页／筛选不影响全账号计数；A1 test_assignment_summary_ignores_list_filters。
- 桌面／移动及未保存备注／指派选择在轮询后保持；A2 浏览器验收。

## A1：负责人版本、通知和计数接口

**Files:** 修改 `app/team_followups.py`、`app/accounts.py`、`app/main.py`；新增 `app/notifications.py`、`tests/test_assignment_notifications.py`；扩展 `tests/test_team_followups.py`。

**Interfaces:** `notifications.initialize() -> None`；`team_followups.advance_owner_revision(c, opportunity_id: int) -> int`；`notifications.record_assignment(c, opportunity_id: int, recipient_id: int, actor_id: int, revision: int) -> None`；`notifications.unviewed_assignment_count(c, user_id: int) -> int`；`notifications.mark_assignment_viewed(c, user_id: int, opportunity_id: int, expected_revision: int) -> int`（返回本次标记行数）；`notifications.record_event(c, recipients: list[int], kind: str, opportunity_id: int, owner_revision: int, event_key: str, payload: dict) -> None`（安全 payload 白名单）。

- [ ] 写上述回归测试及未登录／其他员工／非本人管理员不能确认查看的测试；核心断言为指派后 `count==1`，本人确切版本确认后 `count==0`，过期版本返回 409，其他账号返回 403。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/test_assignment_notifications.py -q`，确认因缺接口／错误状态失败。
- [ ] 新增 `team_followup_revisions(opportunity_id PRIMARY KEY, owner_revision)`，版本跨 release 留存；负责人表增 `owner_revision`、`assignment_source=claim/admin/legacy`、`assigned_by_user_id`。领取、实际转交、放弃递增；重复本人领取／同人指派不递增。
- [ ] 建 `notifications`：`id,user_id,kind,opportunity_id,owner_revision,event_key,payload,created_at,read_at`，`(user_id,event_key)` 唯一。指派计数只 join 当前本人负责人及当前版本的 unread assignment；领取／公开阶段事件通知启用管理员，私人状态／备注事件不发。
- [ ] 提供 `GET /api/my-assignments/summary -> {unviewed_count}`、`PATCH /api/opportunities/{id}/assignment-viewed`（body 为 owner_revision，返回本人剩余数字）；另提供本人通知列表及单条查看接口。当前派发来源在 owner overlay 返回，员工查看状态不进入团队公开字段。
- [ ] 运行相关新测试和 `tests/test_team_followups.py tests/test_followups.py tests/test_regular_user_permissions.py`，结果应全通过；仅本阶段文件记录本地提交。

## A2：侧栏红圈、来源标记和安全同步

**Files:** 修改 `app/static/index.html`、`style.css`、`app.js`、`interactions.js`、`collaboration.js`、`team.js`；新增 `app/static/notifications.js`（按 defer 顺序接入）。

**Interfaces:** `startNotificationPolling()`／`stopNotificationPolling()`；`refreshAssignmentBadge(summary)`；`acknowledgeDisplayedAssignment(item, context) -> Promise<void>`；调用 A1 的摘要／确认接口。context 含 owner、sessionGeneration、drawerVersion、id、owner_revision；所有异步回复先核对。

- [ ] 在隔离服务准备合成公告和管理员／员工；验证现有页面无派发徽标，再实现行为。
- [ ] 在我的跟进导航加独立徽标，0 隐藏、1–99 数字、100 起显示 99+；专属样式不受 `.nav-item > span` 图标样式和移动端隐藏影响；列表加管理员派发及新指派标记。
- [ ] `openDetail` 成功展示并保持同一上下文后确认该版本；打开列表不确认、失败不确认。推送已读操作保持独立。`enterApp/showLogin` 管轮询生命周期，登录可见时 15 秒检查，回复只 patch 合法只读节点。
- [ ] 无负责人显示指派，有负责人显示转交；直接 PUT assignee，不先 POST claim。团队详情显示“已跟进 · 负责人：姓名”，不覆盖管理员个人状态。
- [ ] 浏览器验收计数 2→打开一项→1、标签已读后保留、主动领取不加派发数、重复指派不重置、账号切换不串数；输入未保存备注／阶段／接手人后轮询不覆盖。保存桌面和移动截图。
- [ ] `node --check` 检查全部改动 JS，`git diff --check` 无错误；相关 API 测试通过后记录本阶段本地提交。
