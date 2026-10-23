# Personal follow-up implementation plan

> For agentic workers: use subagent-driven-development for the independent tasks below. This implements the user-approved personal tracking design; team ownership and shared private notes are out of scope.

**Goal:** Add 我的跟进 and show the same current personal follow-up state for an announcement across 今日资讯, 推送列表 and 我的跟进.

**Architecture:** Keep followups keyed by account and announcement ID. Reuse existing PATCH and detail APIs, add a personal saved-record listing, and overlay current personal metadata onto delivery history without modifying its immutable first-delivery evidence.

**Tech stack:** FastAPI, SQLite, vanilla JavaScript/CSS, pytest, native Uvicorn.

## Constraints

- Work locally on the existing branch; do not deploy, commit, push or alter credentials/source/task configuration.
- Keep the first four navigation entries; add 我的跟进 immediately after 推送列表. 监测网站 stays under 个人设置 for regular users.
- Browsing and generating AI alone do not create a personal follow-up. Saving the existing status or note does.
- Both roles only see their own follow-up records. Admin permissions do not grant access to another user's notes.
- Use announcement ID, never title, as identity. Same-title announcements with different source URLs/IDs keep separate records.
- Reuse the existing statuses, saved notes and last-updated column. Do not add a new workflow, remove action, automatic collection or schema migration.
- Preserve first-delivery topic evidence, delivery/read timestamps and deduplication; new deliveries expose the latest personal state.

## Tasks

### 1. Personal list and detail metadata

- [x] Add failing tests for empty/saved-only listing, both-role isolation, title/buyer/note search, status filtering, recent-follow-up sorting, pagination and same-title independence.
- [x] Add GET /api/followups with q/status/page/page_size/sort; list only explicit saved records for the authenticated account, including notices never delivered to it.
- [x] Add has_followup and followup_updated_at to account overlays, retaining existing status/note compatibility.

### 2. Current state in delivery lists

- [x] Add failing tests for follow-up before dispatch, follow-up after dispatch, alternate topic and account isolation.
- [x] Overlay current status/has_followup/followup_updated_at in /api/pushes without rewriting saved payload evidence or timestamps.
- [x] Verify both entry points return the same current personal record for the same ID.

### 3. User interface

- [x] Add 我的跟进 navigation, own query/filter/page state, recent-follow-up default sorting and an informative empty state.
- [x] Show title/source/published time/status/note excerpt/last follow-up time; reuse the existing announcement detail and manual save action.
- [x] Add a current personal status column in the push list; retain read state separately.
- [x] Refresh lists when a detail closes after saving, prevent delayed saves from claiming success in a different user's or announcement's drawer, and retain navigation/scroll behavior.
- [x] Verify desktop/mobile, paging, saved records across reload and account switching; preserve private records and the recent read-only source navigation.

### 4. Validation and delivery

- [x] Run targeted red/green tests, integrated full pytest, changed JavaScript syntax and whitespace checks.
- [x] Restart only the known local Uvicorn process; verify the feature as xyh without altering real personal records for QA.
- [x] Use an isolated temporary database/service for browser save-and-sync verification if xyh has no suitable preexisting records.
- [x] Save a screenshot and leave the main local tab signed in as xyh at 我的跟进.

## Verification results

- Full pytest: 199 passed; one existing Starlette/httpx deprecation warning.
- All six served JavaScript files passed `node --check`; tracked diff whitespace check passed.
- Isolated browser QA verified saved-only listing, note search, status/sort, numeric page jumps, persistence, account isolation and current state across today, historical pushes and follow-ups.
- With PATCH delayed by 600 ms, closing, changing views, reopening an announcement, switching accounts and opening the account menu retained the correct state and focus. Another announcement's unsaved draft remained intact.
- Independent review passed after correcting pending-save, draft-overwrite, pagination-boundary and popup-focus issues.
- Main local service remains at port 8000, signed in as xyh on 我的跟进 with zero saved records. The isolated port 8001 service was stopped. Desktop and narrow-layout checks passed, and no real personal follow-ups were changed for QA.
