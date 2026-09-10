# Task Plan: Update, configure, and run ReAgent

## Goal
Bring the local ReAgent checkout up to date with `origin/main`, apply the supplied environment configuration in the project-prescribed location, and start and verify the service.

## Current Phase
Frontend implementation: final regression and delivery (2026-09-11)

## Frontend implementation — current request

- [x] Reconfirm the audit evidence, source entry points, and baseline working-tree scope.
- [x] Add initial failing regression coverage for stable stream identity and safe front-end entry points.
- [x] Implement the smallest safe fixes, expanding test coverage for distinct state/visual behaviors as each is addressed.
- [x] Verify backend/API, static assets, and real-browser desktop/mobile flows against the audit acceptance criteria.
- [ ] Commit only source, dependency, and planning deliverables; push the implementation branch to `origin`.
- Scope: implement the approved audit plan. Do not include `.env`, audit databases, screenshots, browser cache, or existing user data in Git.

## Frontend audit — current request
- [x] Inspect local UI in a real browser and capture desktop/narrow-viewport evidence.
- [x] Exercise safe Agent execution/trace interactions and distinguish observed defects from code risks.
- [x] Write `docs/frontend-audit-plan.md` with priorities, implementation scope, colour tokens, and acceptance criteria.
- Frontend audit deliverable completed on 2026-09-11; implementation is complete and ready to be committed from `codex/frontend-execution-ux`.
- Scope: preserve credentials, existing sessions, browser artifacts, and previous user data; commit only the implementation, tests, dependencies, and planning documents.
- Keep previous setup/pull findings below as historical context; this audit targets the currently checked-out revision.

## Phases

### Phase 1: Inspect repository and requirements
- [x] Confirm repository, branch, remote, and clean working tree
- [x] Read the startup and configuration instructions
- [x] Inspect supplied configuration without exposing secret values
- **Status:** complete

### Phase 2: Update source and install dependencies
- [ ] Fetch and fast-forward from `origin/main` (blocked: Git transport cannot connect)
- [x] Repair remaining runtime dependency manifest gaps and reinstall in `.venv`
- **Status:** in_progress

### Phase 3: Configure and run
- [x] Place the supplied configuration at the documented path
- [x] Validate configuration with `app.config.Settings`
- [x] Start the documented service
- **Status:** complete

### Phase 4: Verify and hand off
- [x] Run the full offline test suite
- [x] Restart the service to load the final dependency set
- [x] Verify health and record startup command/endpoint
- [x] Record the startup command and endpoint
- **Status:** complete

## Delivery Status
- Service configuration, dependency setup, startup, and verification are complete.
- Source synchronization remains blocked: terminal Git transport could not connect to `github.com:443` in three distinct attempts, so the checkout remains at commit `894140bc0bc90e2967263e42db2a74c26f15f497` (2026-09-07). No pull occurred.

## Decisions Made
| Decision | Rationale |
|---|---|
| Preserve local files and fast-forward only | The checkout was clean and the request is to pull remote updates. |
| Inspect environment variable names but never print values | The supplied file may contain credentials. |
| Do not copy `env.txt` verbatim to `.env` | Its nine generic labels do not match the project's environment-variable names; map them explicitly. |
| Map the two supplied model/URL blocks to LLM and embedding respectively | The supplied model identifiers are `deepseek-v4-flash` and `Qwen3-Embedding`; the app exposes matching configuration groups. |
| Reuse the supplied API credential for both model clients | There is one supplied API credential following both configuration blocks. |
| Use QQ-address and token fields as SMTP user and password | The project's SMTP tool documents an email address plus QQ authorization code; its `account` field has no project setting. |
| Enable memory | An embedding model and endpoint were supplied specifically for this capability. |

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| `git fetch origin main` connection reset | 1 | Inspect local docs and retry with HTTP/1.1 before a fast-forward pull. |
| GitHub port 443 connection timeout | 2 | Check repository reachability through an independent HTTP request; continue configuration only if source sync remains unavailable. |
| Git transport cannot resolve `origin` despite GitHub HTTP responding | 3 | `git ls-remote` also timed out. Do not use a non-Git download as a substitute for the requested pull; continue with the verified local commit. |
| Windows `py` launcher is unavailable | 1 | Use the verified default `python` 3.12.7 executable to create the environment. |
| Initial dependency install was interrupted by the execution time limit | 1 | Reuse the created `.venv` and resume `pip install -r requirements.txt`; verify imports afterward. |
| Combined background-server command rejected by the execution policy | 1 | Split listener detection, hidden process launch, and health verification into small, independently permitted steps. |
| Uvicorn exits before binding port 8000 | 1 | Root-cause investigation shows `app.memory.repository` unconditionally imports `sqlite_vec`, but `requirements.txt` omits its `sqlite-vec` package. Add the missing runtime dependency after a minimal failing import check. |
| Entry import fails after sqlite-vec repair | 1 | Root-cause investigation shows `app.mcp.client` imports the official `mcp` SDK unconditionally, but `requirements.txt` omits its `mcp` package. Add it only after the minimal `import mcp` check fails. |
| Full test suite shows MCP and document-tool dependency failures | 1 | MCP tests use v1 `FastMCP`, so cap dependency at `<2`; document tools import PyMuPDF (`fitz`) and `openpyxl`, which are both absent from the manifest. |
| Planning-file update context mismatch | 1 | Read current tracking files and applied a precise follow-up update. |
