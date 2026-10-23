# Regular-user workspace implementation plan

> For agentic workers: use subagent-driven-development for the independent tasks below. The approved design is the role boundary agreed with the user in this conversation on 2026-10-13.

**Goal:** Provide a regular-user workspace for xyh with shared public notices and private subscriptions and deliveries, while administrators retain collection management.

**Architecture:** Keep the existing admin/user roles and public notice database. Enforce role checks in management APIs, return a minimal source catalogue for personal filtering, and render only personal automation for regular users. Personal subscription settings must not control public collection.

**Tech stack:** FastAPI, SQLite, vanilla JavaScript/CSS, pytest; native local Uvicorn development.

## Constraints

- Work only on the existing local feature branch; preserve existing local data, credentials, accounts, subscription settings and follow-up notes.
- Do not deploy, commit, push, create accounts, reset passwords, or add a new role.
- Keep the first four navigation items: 今日资讯, 订阅情况, 自动化任务, 推送列表.
- Regular users retain 系统配置 (own subscriptions and push plan) and 我的账号.
- Monitor rules, collection schedules, collection logs and manual collection are administrator-only.
- Ordinary users can view a read-only monitoring-website directory with public names, types and website links; this was approved after the initial role split.
- All roles can read public notices and public AI explanations; only administrators can generate/replace public AI explanations. Own-topic AI and own follow-up records stay private and available.
- Public collection uses administrator-owned global collection settings and source rules, not personal subscription keywords of any account.
- Keep the local scheduler disabled and preserve the administrator experience.

## Tasks

### 1. API permissions and public data

- [x] Add failing API tests for regular-user denial of collection management reads and writes; allow minimal source catalogue and public notice data.
- [x] Enforce admin access to full source configuration, collection schedule, progress and runs; sanitize dashboard for regular users without leaking those records.
- [x] Deny regular-user public AI writes while retaining own-topic analysis and private follow-up access.
- [x] Verify administrator endpoints still work and account isolation remains intact.

### 2. Separate collection from personal subscriptions

- [x] Add failing tests proving personal subscription keywords/exclusions cannot change global collection recall or bypass administrator exclusions.
- [x] Remove the subscription-to-global-collection dependency; preserve global focus keywords, source-specific rules and bounded collection.
- [x] Correct preview/help wording so personal subscription settings describe matching already ingested public notices.
- [x] Verify personal matching and dispatch still operate normally.

### 3. Regular-user interface

- [x] Hide administrator navigation and reject direct client routes for regular users.
- [x] Render personal automation only; do not request management endpoints for regular users.
- [x] Show a public data freshness summary on Today instead of collection management, progress polling or collection notifications.
- [x] Use the minimal source catalogue in personal subscription forms; preserve name-based source filters and original notice links.
- [x] Clarify public versus own-topic AI and hide the public generation action for regular users.
- [x] Retain the administrator interface and the recent compact forms, labels, layout and navigation changes.

### 4. Integration and validation

- [x] Run the appropriate backend tests, then the full suite once changes are integrated; check changed JavaScript syntax and Git whitespace.
- [x] Restart only the known local Uvicorn process after backend changes.
- [x] Sign in as the user-provided xyh account and verify shared notices, personal topic configuration and personal automation; avoid modifying real preferences or records.
- [x] Check direct restricted routes and mobile layout, and verify the admin experience using an independent authenticated session.
- [x] Save a screenshot of the regular-user workspace and leave its browser tab signed in as xyh for review.

### 5. Read-only public source directory

- [x] Add a login-protected public-source endpoint returning only id, name, url, kind and enabled; include active sources and historical contributors, including secondary observed sources, without exposing rules or runtime information.
- [x] Add regular-user 监测网站 navigation and a read-only view while retaining the full administrator source manager and its protected API.
- [x] Test anonymous access, metadata whitelist, inactive historical contributors, unused-source exclusion and unchanged administrator permissions.
- [x] Check JavaScript and relevant backend tests, restart the local service, and verify the directory as xyh on desktop and mobile without modifying source configuration.
