# Findings & Decisions

## Mist Mint implementation (2026-09-11)

- User authorized implementation of the approved Mist Mint × Developer Studio plan.
- Isolated worktree created from `39cc4dc` after the project-local `.worktrees/` ignore rule was committed on main.
- M0 must rerun the complete regression suite before any application edit and add focused RED tests before each behavior change.
- The design/implementation plan remains in the parent checkout at `D:\CodeSource\harness\agent-runtime\docs\reagent-mist-mint-developer-studio-execution-plan.md`; treat it as approved scope and source mapping.
- Baseline attempt initially failed during test collection with `ModuleNotFoundError: mcp`. Investigation shows both root and worktree interpreters use their own `.venv`, both now expose `mcp 1.30.0`, and `pip show` locates the package in the worktree. The pip installation command was still completing around the first test attempt; this is an environment-readiness race, not an application or manifest defect. Hypothesis: rerunning the focused MCP test after installation completion will pass without a source change.
- M2 keeps the established runtime DOM IDs and API routes intact while adding a global header (`#global-header`), primary rail (`#app-rail`), contextual views, and stateful Inspector section/detail tabs. The resource destinations intentionally remain explicit empty states until M5 wires their data interactions.
- Browser evidence at 1536×1024 shows the intended three-column Developer Studio structure. Rail navigation, primary Tools view, Timeline/Trace detail switching, and outer Files switching all update state without console errors beyond the pre-existing favicon 404.
- At 390×844, the off-canvas Inspector's `translateX(100%)` enlarged document scroll width to 748px. The smallest safe repair is horizontal clipping on the mobile document while retaining vertical scrolling; a second check found mobile min-heights expanding the page and hiding the composer, so the mobile flex shell now bounds `main`, `workspace`, and `chat-column` to the viewport.
- M3 browser runs found and corrected a latent Welcome residue: the static empty-state remained above the first user/assistant pair because `send()` never removed it. `addMessage("user", ...)` now performs that one-time removal, while `newSession()` and initial load both use `renderWelcome()`.
- The original assistant copy action captured the assistant's initially empty string, and `send()` selected `div:last-child`. The replacement uses a stable `.md-body` reference and reads that element's current text at click time; absence or failure of the clipboard API reports a recoverable failure label.
- A completed calculator run previously copied a full Trace tree and a second copy of tool calls into Chat. Chat now keeps the one real tool card plus a compact run summary; the summary selects the existing Trace/Timeline Inspector tab instead of inventing another data path. The post-change desktop screenshot confirms the expected hierarchy.
- M4 retains one Trace data path: a live `done` event supplies the tree directly and a history replay resolves the existing trace endpoint. Switching/replaying invalidates and clears the previous tree first, so a stale trace cannot remain visible while data loads.
- Context and Agent tabs now distinguish actual zero-data states from populated states. Agent dependency rows are derived solely from `depends_on`; no sequential arrow is invented for independent Agent steps.
- M5 resource pages store only already-returned API fields in front-end state. Search does not fetch more entities, messages, or server-side results. Session alias/pin metadata is best-effort localStorage data, intentionally scoped to this browser and safely ignored when JSON is unavailable or malformed.

## Frontend audit (2026-09-10)
- Final report `docs/frontend-audit-plan.md` written on 2026-09-11: F01–F16, evidence classifications, priorities, theme tokens and contrast calculations, event/identity requirements, T0–T6 execution order, code-entry links, and acceptance matrix.
- 1024×768 check: main width 744px, input width 628px, fixed sidebar remains 280px. 12 screenshots in total. No physical mobile keyboard or real-provider quality testing performed.
- Controlled browser fixtures completed: pending tool displays green check + waiting, zero workflow panels and empty running status; stop leaves `.msg.streaming` on the answer and tool still waiting; HTTP 503 JSON produces no error message while header remains connected.
- Harmless local Markdown fixture confirms untrusted HTML event-handler execution in rendered assistant content. Only a DOM marker was set; no credentials or external destinations accessed. Prioritise output sanitisation before any expanded deployment.
- Captured `09-controlled-pending.png`, `10-controlled-stopped.png`, and `11-controlled-http503.png`. Fixtures are explicitly synthetic response states; do not call these real provider/network tests.
- Controlled slow-stream fixture requires stop button by title: its accessible name is the symbol `■`, not the title text. First fixture attempt timed out on locator; production code unaffected. CLI multi-line source also required flattening (excluding comments) on Windows.
- Static contrast calculation: existing `--text-dim #94A3B8` over assistant background `#334155` is 4.04:1 (below normal-text AA when used there); sidebar/base separation is 1.22:1 (an aesthetic hierarchy issue, not a text compliance claim). Most normal text on dark backgrounds is not inherently low-contrast.
- Proposed fresh palette uses near-white/mint surfaces, dark ink, and deep teal actions; verified white on #0F766E=5.47:1, #0F766E on #E7F6F1=4.91:1. This is a proposal, not a CSS change.
- Measured long-conversation workflow heights: all three ~1.14px; computed flex-shrink=1 and overflow=hidden. Tool cards shrink to ~21px. This is reproduced clipping, not merely dense information.
- Real orchestration with two independent `depends_on:[]` steps renders `researcher → analyst` anyway; a false serial dependency is displayed. `08-parallel-orchestration.png` isolates the detail view using the same stored run.
- Accessibility acceptance references verified from W3C: normal text 4.5:1, large text 3:1; visible keyboard focus; target size 24px minimum subject to criterion exceptions. Recommended project touch target 44px is a design choice, not the AA minimum.
- Real calculator division-by-zero: red X appears, but expanded tool body still says waiting and omits error message (answer text does mention divide-by-zero). `06-tool-error.png`.
- Real Plan request completed two weather steps. Long conversation reveals a more severe layout defect: older cards/workflows collapse into thin bars due to flex shrinking with overflow hidden; investigate measured heights. `07-plan-complete.png`.
- Real isolated `/orchestrate` created two successful agents with both `depends_on:[]` (parallel plan); inspect its UI rendering next. No external tool was requested.
- Repeated real `get_weather` calls (Beijing, Shanghai) reproduce name-based overwrite: Beijing remains green success + waiting forever after request completes; Shanghai gets the final output. `05-repeat-tool.png`.
- Live-answer copy button wrote an empty string, confirmed by clipboard read in the isolated browser after granting permission. Root cause: addMessage registers closure over initial empty answer; ensureCopyButton returns early because button exists.
- Diagnostic notes: clipboard read initially waited for permission; granting permissions completed it. One guessed file name `plan_executor.py` did not exist; actual references point through runtime's imported executor instead.
- Root Trace caret clicked via real browser: children remain `display:block`, all 12 child rows remain; collapse defect reproduced.
- Opening the new audit session removes every `.workflow` and replays calculator with `{}` arguments and no timing. Screenshot `04-session-replay.png`.
- CLI run-code expects page directly, not `{page}` destructuring (initial copy check failed before interaction); subsequent scripts will use the supported signature.
- Real isolated ReAct calculation succeeded (123*456=56088). Screenshot `03-react-complete.png`: welcome remains after send; answer precedes tool cards in DOM; same call appears again inside final workflow; collapsed tool card is ~105px tall because structured HTML inherits `white-space: pre-wrap`.
- Trace root and gateway caret next siblings are `.tn-detail`, while collapse handler only accepts immediate `.tn-children`; visible root-caret click test pending. Interactive spans have tabIndex=-1 and no button role.
- Mobile 390×844 reproduced severe layout failure: fixed sidebar still 280px, input width ~25px, welcome text clipped/narrow vertical wrapping and send text wraps. Screenshot `02-mobile-home.png` inspected.
- Isolated backend launched on 8001 with stub LLM/embedding, memory disabled, isolated DB/trace/sandbox, and no MCP connection. Main service on 8000 untouched.
- One CLI fill used a stale reference after navigation; it failed without submission. Read the new navigation snapshot before retrying.
- Desktop screenshot `01-desktop-home.png` inspected: predominantly dark navy, dense low-emphasis sidebar, conspicuous native white tool scrollbar, mostly empty centre with no example tasks. Console error is only missing favicon (404).
- Code hypotheses to reproduce: pending tools created as `success:true`; update by tool name rather than call ID; tool error bodies only updated when `data.data` is truthy; final workflow created only after stream ends; history drops workflow and forces tool success; abort leaves streaming class; Markdown parsed straight into innerHTML.
- Execution checks will use an isolated stub-backed instance and controlled browser-response fixtures, not user credentials or external tools. These evidence categories will be labelled separately in the report.
- Real Chromium browser opened the running local page successfully. Initial console contains one error, pending classification.
- Static layout: 280px fixed sidebar; no `@media` rules in stylesheet; workflow body has a 420px nested scroll cap. Colour system starts at #0f172a / #1e293b / #334155 with cyan accent.
- CLI wrapper is Bash-only, so Windows uses its exact `npx --package @playwright/cli playwright-cli` equivalent. Help produced a Node cleanup assertion after output; the actual browser-open command succeeded.
- User requests real local-browser inspection of execution visualisation and fresher/more energetic colours; deliver an actionable Markdown plan, not an implementation.
- Baseline: local checkout `894140b`; earlier source pull did not succeed. Preserve existing changes.

## Frontend implementation (2026-09-11)

- TDD RED confirmed before application edits: the SSE stream begins with `step`, has no `run_started`, exposes neither event/run metadata nor `tool_call_id`, and only communicates `success`; the new identity/lifecycle test failed precisely at those missing fields.
- The static asset contract also fails because the UI has no central `renderMarkdown` or `finalizeRun` entry point and lacks the required composition guard. This matches the browser-audit root causes rather than a test setup error.
- `ToolCallRequest` already owns the authoritative provider/tool identity as `id`; emit that as `tool_call_id` rather than inventing a tool-name key. A per-request web run ID and monotonic sequence are sufficient for the browser's lifecycle reconciliation without changing persisted message schemas.
- Initial isolated-browser verification after implementation: a real Stub ReAct calculation renders one completed “本次执行” card before the final answer, keeps the completed tool call within that card, and shows the mint-white/deep-teal palette. The previous duplicated standalone tool card and delayed duplicate workflow are absent in this path. Screenshot: `output/playwright/implementation-audit/01-react-complete.png`.
- Browser checks confirm the root Trace caret now hides its child tree and exposes an “展开子步骤” accessible name. The actual assistant-copy action now writes `计算结果：56088.0。`, not an empty string. A real same-turn Beijing/Shanghai weather run creates two separate completed `get_weather` cards with their own arguments and durations.
- The real divide-by-zero run now gives the calculator card a failed icon/status (not a green success state); its Trace also records the tool error. Controlled browser states confirm cancellation clears the streaming cursor and marks the queued tool `cancelled`; a JSON HTTP 503 produces visible error text and a failed run summary; raw `<img onerror>` and `javascript:` Markdown links produce no image, no unsafe link and no event-handler execution.
- At 390×844, the main pane retains a full-width 44px composer and menu button; opening the sidebar overlays it rather than shrinking the input. The Escape regression test was RED then GREEN, and an actual mobile-browser Escape press closes the drawer (`#sidebar.is-open === false`).
- Real isolated orchestration verification: API returned two successful steps with `depends_on: []`; the UI rendered one dependency layer and zero inter-layer arrows (`layers=1`, `arrows=0`). The visible cards label both researcher and analyst “可并行”, rather than drawing a false researcher→analyst chain.
- Reloading a real persisted session now reconstructs one honest “历史执行” panel for each tool-using turn. It restores the original calculator/Beijing/Shanghai arguments and retains the failed divide-by-zero status, while explicitly labels Trace/plan as not recorded instead of fabricating a success workflow.

## README rewrite and integration (2026-09-11)

- The current `README.md` is a 20 KB, stage-by-stage learning and interview-oriented guide rather than a conventional project entry point. It contains a useful Mermaid architecture diagram and an orchestration sketch that should be retained in a lighter, current form.
- Its claims and commands need refresh: it advertises `240 passed` while the current suite has 260 passing tests; it includes a demo-video placeholder; and `cp .env.example .env` is not Windows-friendly for the active development environment.
- The replacement README should lead with what ReAgent is, its core capabilities, prerequisites, quick start, configuration, Web UI/API usage, architecture, repository layout, testing, deployment, security, and contribution guidance. Stage-by-stage learning and interview material belongs in `docs/`, not in the primary project entry point.
- `git fetch origin --prune` failed once in this phase with a GitHub port 443 connection error after the feature branch had already been pushed. Treat remote synchronization as pending and revalidate `origin/main` immediately before merge/push; do not assume the local remote-tracking ref is current.
- The original README is UTF-16 LE. `apply_patch` cannot read that encoding, so the replacement was first created as a validated UTF-8 temporary file and then moved over the explicitly requested `README.md`. This intentional encoding normalization makes the README standard GitHub Markdown; Git reports the migration as a binary diff relative to the old UTF-16 blob.
- The replacement contains two Mermaid diagrams, seven checked local Markdown links, portable Windows/POSIX setup commands, current API routes, Compose instructions, and no hard-coded test count or placeholder demo claim.
- Fresh remote inspection shows `origin/main` advanced from `894140b` to `0fa1faf` through a substantial execution-workbench series (30 files, including persisted execution state, retry/cancel lifecycle events, frontend timeline behavior, and dedicated tests). The overlap with this feature branch is concentrated in `app/api/web.py`, `app/static/*`, and `tests/test_web.py`.
- Integration rule: fast-forward local `main` first, then resolve overlapping implementation conflicts in favor of the newer remote `main`; retain this branch's README rewrite, dependency manifest repair, planning documents, and any non-conflicting additions. This merges branch history without regressing upstream lifecycle work.
- The merge preview exposed one remote baseline test-harness issue on Windows: `tests/test_frontend_events.py` only stripped LF-delimited initialization calls from JavaScript, while the checked-out file uses CRLF. Updating that regular expression to accept `\r?\n` fixes the VM isolation without changing application behavior. The focused test passed, then the complete merged suite passed with 282 tests.

## Requirements
- Pull upstream updates from `https://github.com/cplike1017/ReAgent` into this local checkout.
- Use `C:\Users\15837\Desktop\env.txt` to configure the project.
- Start and verify the configured project.

## Research Findings
- Current working directory is the ReAgent repository on branch `main` with `origin` set to the requested GitHub repository.
- The initial working tree was clean.
- Documentation specifies `pip install -r requirements.txt`, maps configuration to a root `.env`, and starts the web UI with `python -m uvicorn app.main:app --port 8000` at `http://localhost:8000/`.
- The full multi-process stack is `docker compose up --build` (API, Redis, worker); it also reads the root `.env` if present.
- The supplied file has nine assignments with generic labels (`model`, `url`, `api`, SMTP-related labels). They require explicit mapping instead of a raw copy.
- The supplied model identifiers show a natural mapping: `deepseek-v4-flash` is the chat LLM and `Qwen3-Embedding` is the embedding model. The one API credential is shared by both blocks.
- ReAgent auto-selects OpenAI-compatible clients only when a base URL and API key are present. It accepts a root `.env` using standard `LLM_*`, `EMBEDDING_*`, and `SMTP_*` names.
- ReAgent's QQ-mail tool needs `SMTP_HOST`, `SMTP_USER`, and `SMTP_PASSWORD`; the source provides host, a QQ email address, and an authorization token. The extra `account` source value is not used because the project has no matching setting.
- A root `.env` now exists and contains the project-recognized LLM, embedding, memory, and SMTP settings. It is expected to be Git-ignored.
- Dependencies from `requirements.txt` are installed in `.venv`; `pip check` reports no broken requirements.
- Application configuration validation resolved both model providers to `openai`, recognized `deepseek-v4-flash` and `Qwen3-Embedding`, and confirmed all required SMTP fields are set. No configuration value was printed.

## Technical Decisions
| Decision | Rationale |
|---|---|
| Read project documentation before updating or starting | Startup method and target environment-file path must come from the project. |
| Prefer the documented local Web UI for initial startup | It avoids requiring Docker/Redis/worker and is explicitly supported by the README. |

## Issues Encountered
| Issue | Resolution |
|---|---|
| GitHub connection reset during first fetch | Will retry using Git HTTP/1.1. |
| GitHub connection timeout during HTTP/1.1 fetch | Verify current commit and independent GitHub reachability; do not claim the checkout is updated unless a fast-forward succeeds. |
| Git transport unavailable | A GitHub HTTPS HEAD request returned 200, but both fetch modes and `git ls-remote` fail after about 21 seconds. Current checkout remains commit `894140b` (2026-09-07). |
| Windows Python launcher unavailable | The default `python` is Anaconda Python 3.12.7 and has pip 24.2, so it can create the project virtual environment directly. |
| Dependency installation incomplete | `.venv` exists and pip is functional, but `fastapi` is not installed yet; resume in the existing environment. |
| Server launch command rejected before execution | This was a host execution-policy rejection, not an application failure; retry in smaller steps with the same hidden-window constraint. |
| Uvicorn startup failure | Reproduced in the foreground: import of `app.main` fails with `ModuleNotFoundError: sqlite_vec`. The memory repository imports it unconditionally and calls it to load SQLite vector support, while `requirements.txt` omits `sqlite-vec`. |
- The minimal failing check (`import sqlite_vec`) was observed before the manifest change. Adding `sqlite-vec>=0.1.6` to `requirements.txt` and reinstalling supplied version 0.1.9; the import now succeeds and `pip check` is clean.
- After the sqlite-vec repair, `app.main` import reaches `app.mcp.client` and fails because the official `mcp` SDK is also omitted from `requirements.txt`. Both runtime and test code import it directly.
- The minimal `import mcp` check failed before the change. Adding `mcp>=1.0` installed current version 2.2.0; `import mcp`, `import app.main`, and `pip check` now pass.
- The Uvicorn service is running on the documented local port (8000); its health endpoint returned HTTP 200.
- Full tests identify two manifest corrections: the project's imports/tests rely on MCP v1 APIs (`mcp.client.sse`, `FastMCP`), so the requirement must constrain `mcp<2`; the document-reading runtime imports `fitz` (PyMuPDF) and `openpyxl`, so both distributions belong in the runtime dependencies.
- Installing `mcp>=1.0,<2`, `PyMuPDF>=1.24`, and `openpyxl>=3.1` installed MCP 1.30.0, PyMuPDF 1.28.2, and openpyxl 3.1.5. The focused MCP/document test set now passes: 19 passed.
- Full offline test suite passes after the manifest repairs: 258 passed (one third-party deprecation warning). The live Uvicorn process predates the MCP downgrade, so it should be restarted once before handoff.
- After restart, `GET /health` and the Web UI root both return HTTP 200. The service is available locally at `http://localhost:8000/`.

## Resources
- `README.md`
- `.env.example`
- `docker-compose.yml`

## Visual/Browser Findings
- None.
