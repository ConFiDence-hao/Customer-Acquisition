# 直接智能操作与推送诊断实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 智能查询直接展示搜索结果，填写直接填入并可撤销，主题诊断提供可验证的无推送原因。

**Architecture:** QuerySpec 统一过滤，再接现有真实提交函数；辅助结果来自 B2 的受控队列。诊断先程序统计，AI 只解释已核验数据，不自动改配置。

**Tech Stack:** FastAPI、SQLite、Pydantic、原生 JavaScript、pytest、CUA。

**Spec:** [工作流设计第一、二、六节](../specs/2026-10-14-direct-ai-workflows-design.md)、[全局约束](2026-10-14-direct-ai-workflows.md)；依赖 B1 时间、B2 jobs、B3 项目跟进 AI 授权接口。

## Global Constraints

- 总计划全部约束适用；填写永不自动保存，诊断永不自动放宽条件。
- 支持矩阵严格按 spec；q 和 terms 是同一个关键词条件，新的智能或手动词替换旧词。
- 相对查询键含可信参考日期及规范化范围，交互结果 5 分钟有效，跨日失效。

## Review Focus

- “未截止”不能等同可报名，未知截止不能假装有效；C1 测试明确区分。
- 旧 q 与新 terms 不得误叠加 AND；C1 test_keyword_facet_replacement。
- 推送时间与公告发布时间不同，翻页和导出不得丢条件；C1 过滤一致性测试。
- 禁用控件与 busy 可能阻止自身自动填入，晚响应可能覆盖新草稿；C2 延迟响应与锁释放验收。
- last_count=0 或其他主题已送达不等于无匹配，AI 不读私人内容；C3 诊断测试。

## C1：统一查询字段和过滤

**Files:** 新增 `app/querying.py`、`tests/test_querying.py`；修改 `main.py`、`workbench.py`、`accounts.py`、`management.py`、`assist.py`、`static/app.js`、`interactions.js`、`workspace.js`、`workbench.js`。

**Interfaces:** `QuerySpec` 含 `q,terms,term_mode,published_from,published_to,pushed_from,pushed_to,deadline_state` 及对应页面现有字段；`normalize(page: str, fields: dict, reference: datetime) -> QuerySpec`；`matches_notice(item: dict, spec: QuerySpec, now: datetime) -> bool`；QuerySpec 的 `to_params() -> dict` 提供唯一活动关键词条件及完整持久筛选。兼容 q 的原字段范围，terms 可匹配标题／正文。

- [ ] 编写 keyword 替换、OR/AND、边界日期、未知时间、未截止／报名过期、后续结果覆盖、本人状态权限、推送两种时间及分页／导出一致性测试；规范化后断言旧 q 已被新 terms 替换，不产生两个独立词条件。
- [ ] 运行新测试确认失败；扩展后端 Pydantic/辅助白名单，按页面校验标准地区／阶段和日期范围。所有筛选在分页前，共用 QuerySpec；不支持字段明确拒绝，不能删掉后查询。
- [ ] 接通 state、URL 参数、手动表单及条件标签；修改关键词仅替换这一 facet，其他合法条件保留；导出也使用同一规范化条件。
- [ ] 为查询任务提供可信参考时间及专题目录，5 分钟／跨日结果不自动应用；同界面仍有效时最多以新参考点重建一次。订阅本地搜索和旧企业表单严格用自己的支持范围。
- [ ] 相关新测试及 API/workbench/权限旧测试通过，记录本阶段本地提交。

## C2：理解后直接查询／填入

**Files:** 修改 `app/static/assist.js`、`app.js`、`workbench.js`、`workspace.js`、`interactions.js`、`monitoring.js`、`style.css`、`app/assist.py`；扩展 `tests/test_assist.py`。

**Interfaces:** `captureAssistContext(form) -> object`、`assistContextCurrent(context, form) -> boolean`、`applyAssistFields(form, fields, context) -> number`、`executeAssistQuery(form, context) -> void`；使用 B2 的 POST/GET jobs 与 C1 QuerySpec；执行走表单 requestSubmit，不将返回 void 当异步操作成功。

- [ ] 后端测试验证字段白名单、scope、项目跟进 AI 权限、敏感字段不生成及不支持查询明确失败；运行确认必要行为先失败。
- [ ] 为五类登记查询及我的跟进接 jobs，统一 owner/session/render/view/tab/entity/request/form_revision 检查；旧回包、手改字段或关闭上下文不应用。自己的控件锁先释放，再核对后填入。
- [ ] 我的跟进登记 `followup-filters`／target `search_followups`，同步扩展 AssistRequest Literal、SPECS 及 PAGE_POLICIES。跟进填写必须带 opportunity_id 和 owner_revision，提交／执行／轮询结果均校验管理员或实时当前负责人，即使该草稿任务为 private 也不能绕过项目权限。
- [ ] 查询自动应用一次、关闭辅助弹窗并一次 requestSubmit；推送实际 handler 在 interactions.js。填写自动落到表单并显示摘要与撤销，保存仍原按钮；来源填写使预览令牌失效。
- [ ] 隔离服务以合成数据／假 provider 验证今日资讯、推送、订阅及跟进直接搜索，填写自动填入／撤销；600 ms 延迟时切账号／切页／开另一公告／改字段不串内容；保持备注、接手人选择和焦点。
- [ ] `node --check`、相关后端测试及 diff 检查通过，保存直接操作截图，记录本阶段本地提交。

## C3：主题推送原因诊断

**Files:** 新增 `app/subscription_diagnosis.py`、`tests/test_subscription_diagnosis.py`；修改 `subscriptions.py`、`workbench.py`、`static/workbench.js`、`interactions.js`。

**Interfaces:** `subscriptions.match_decision(item: dict, topic: dict) -> dict`（含 allowed/reason/hits，原 match 与诊断复用）；`subscription_diagnosis.collect(topic_id: int, user: dict) -> dict`；`enqueue_explanation(topic_id: int, user: dict) -> dict`；handler kind `diagnosis`，调用 B2 队列。

- [ ] 测试真实匹配与漏斗一致、单条件去除预览、暂停／首次待查／来源不足、正文命中、其他主题已推与待关联、管理员团队统计安全及普通用户跨账号拒绝；断言 last_count==0 不决定 no_matches，解释 payload 无备注／阅读状态。
- [ ] 运行新测试确认失败；按 spec 来源—地区—阶段—关键词—标题排除统计，读取实际 subscription_checks／automation 及推送关联。只显示当前待关联数量，不编造历史新增关联时间。
- [ ] 接 `GET /api/subscriptions/{id}/diagnosis` 和 `POST .../diagnosis/explain`；程序结果即时返回，AI 用安全统计和公开样本生成解释；改条件只给预览，仍由用户填写／保存。
- [ ] 主题详情加查看推送诊断；已选主题的空推送列表加同入口，未选主题时引导选择待诊断主题；失败仍显示程序统计。授权与晚响应保护同其他入口。
- [ ] 新测试、initial-push/subscription-recall/workbench 旧测试、JS 检查通过；验证零匹配、暂停、已送达三类页面解释，记录本阶段本地提交。
