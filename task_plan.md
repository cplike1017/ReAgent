# Task Plan: Update, configure, and run ReAgent

## Mist Mint × Developer Studio implementation (2026-09-11)

## Goal

Implement the approved Mist Mint × Developer Studio front-end refactor on `codex/mist-mint-developer-studio` without changing Web API or Agent Runtime contracts, and verify every visible behavior with regression coverage.

## Current Phase

Complete — implementation and final verification recorded.

## Delivery phases

- [x] M0: Isolated baseline, fixtures, and failing regression contracts.
- [x] M1: Semantic Mist Mint tokens and accessible visual foundation.
- [x] M2: Developer Studio shell, navigation, tabs, and responsive drawers.
- [x] M3: Chat, tool cards, composer, and welcome states.
- [x] M4: Evidence-backed execution inspector, trace, graph, context, and summaries.
- [x] M5: Resource views, local session organization, and loaded-content search.
- [x] M6: Dark theme, responsive/a11y completion, full verification, and documentation.

## Constraints

- Worktree: `D:\CodeSource\harness\agent-runtime\.worktrees\mist-mint-developer-studio`.
- Branch: `codex/mist-mint-developer-studio`, based on `39cc4dc`.
- Preserve the Web API, Agent Runtime, persistence models, and user data. Production changes stay in `app/static/` unless an explicitly approved scope expansion is required.
- Follow RED → GREEN → refactor for every behavior change; keep a passing baseline before implementation.

## M0 verification

- Focused MCP verification: 9 passed after the initial dependency-install readiness race.
- Complete baseline: 282 passed, 1 third-party AnyIO deprecation warning, 137.91s.
- First implementation slice: semantic Mist Mint token contract (RED pending) before CSS edits.
- M1 RED → GREEN completed with `test_web_uses_mist_mint_semantic_theme_tokens`; semantic light/dark tokens and contrast-safe CTA tokens now replace the legacy blue palette.
- M2 RED → GREEN completed with `test_web_exposes_developer_studio_shell_contract`; the desktop shell has a global header, primary rail, contextual sidebar, resource workspace, and two-level Inspector tabs while preserving existing runtime IDs and APIs.
- M2 browser verification passed at 1536×1024 and 390×844. A transformed mobile Inspector initially generated horizontal overflow and pushed the composer outside the viewport; both regressions now have focused contracts and are fixed with clipped horizontal overflow plus bounded mobile flex sizing.
- M3 RED → GREEN completed: streamed assistant output now always targets `.md-body`; copy reads the current rendered message and gives a failure state; Welcome/New Task share one renderer and the first user message removes it; a New Task entered from a resource view returns to Chat.
- M3 Composer now exposes only real shortcuts (global-sandbox upload and Tools view) plus a read-only runtime label. Completed runs use a compact Workflow summary that links to Inspector instead of duplicating the entire Trace and tools in Chat.
- M3 browser verification passed with an actual local Stub calculator execution at 1536×1024. The message stack contains user prompt, final answer, one stable tool card, compact workflow summary, and Inspector Trace jump; New Task and 390×844 responsive composer behavior also passed.
- M4 RED → GREEN: the Inspector now renders current/replayed Trace trees through the existing trace endpoint, clears stale Trace state on run changes, hides Context/Agents empty copy when facts exist, and renders dependency edges only when orchestration events declare them.
- M5 RED → GREEN: resource workspaces reuse the loaded Tools, Skills, MCP, Agents, Files, and execution-history responses; files retain global-sandbox semantics and reject oversized/HTTP-failed uploads; session aliases and pins are browser-local; search is keyboard-accessible and filters only loaded entities.
- M6 complete: static asset versions are v26, focus/reduced-motion support remains active, 390px Inspector is fixed-position so it cannot widen the document, browser checks pass, and the final full suite passes (`299 passed, 5 third-party warnings, 132.13s`).

## Goal
Bring the local ReAgent checkout up to date with `origin/main`, apply the supplied environment configuration in the project-prescribed location, and start and verify the service.

## Current Phase
README rewrite and main-branch integration: delivered (2026-09-11)

## README rewrite and main integration — current request

- [x] Inspect the current README, runnable commands, configuration surface, and existing diagrams.
- [x] Rewrite README as a complete normal-project entry point while retaining and updating useful architecture/workflow diagrams.
- [x] Validate Markdown links, commands, and project facts against the repository.
- [x] Run the relevant regression suite, merge `codex/frontend-execution-ux` into local `main`, and rerun verification on the merge result.
- [x] Push `main` to `origin` without force-pushing.
- Scope: documentation and the requested integration only; preserve credentials, runtime data, diagnostic artifacts, and unrelated user changes.

## Frontend implementation — current request

- [x] Reconfirm the audit evidence, source entry points, and baseline working-tree scope.
- [x] Add initial failing regression coverage for stable stream identity and safe front-end entry points.
- [x] Implement the smallest safe fixes, expanding test coverage for distinct state/visual behaviors as each is addressed.
- [x] Verify backend/API, static assets, and real-browser desktop/mobile flows against the audit acceptance criteria.
- [x] Commit only source, dependency, and planning deliverables; push the implementation branch to `origin`.
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
- Frontend implementation is committed on `codex/frontend-execution-ux` and successfully pushed to `origin` after the Fine-grained PAT was configured. The earlier GitHub HTTPS reset, unavailable SSH key, and API TLS timeout are retained in the progress log as resolved delivery diagnostics.
- The primary checkout was switched back to `main` while the implementation was being delivered. To preserve that checkout, the implementation now runs from the separate `D:\CodeSource\harness\agent-runtime-execution-ux` worktree on port 8000; its health endpoint and versioned front-end assets were verified there.
- `codex/frontend-execution-ux` was merged into the updated `main` at `4409de8` and pushed to `origin/main` using HTTP/1.1 after a default-transport connection reset. The merge preserved the newer remote execution-workbench implementation and included the README rewrite, configuration dependency fixes, documentation, and Windows test-harness portability fix.

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
