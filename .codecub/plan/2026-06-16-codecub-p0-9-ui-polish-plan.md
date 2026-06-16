# CodeCub P0.9 Desktop UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve CodeCub desktop visual quality with a three-column workbench, unified visual tokens, restrained translucent surfaces, clearer chat hierarchy, a CodeCub status chip, and a run trail.

**Architecture:** Keep the existing Electron/React data flow and backend JSONL event protocol unchanged. Refactor the renderer project session page into focused presentational components: left project sidebar, center workbench, and right run inspector. Derive visual run progress from existing frontend `chatState` and `events`.

**Tech Stack:** Electron, Vite, React, TypeScript, Vitest, CSS modules via existing `desktop/src/styles/app.css` global stylesheet.

---

## Understood Requirement

The user wants the CodeCub desktop interface to look more polished and professional while keeping the product identity as a restrained code-pet agent. The approved design direction is `Quiet Cub Workbench`:

- 70% professional code agent desktop tool.
- 20% CodeCub pet identity.
- 10% restrained motion/state feedback and light translucent surface treatment.

This plan only changes renderer UI structure, styling, and component-level presentation. It must not change backend behavior, model calls, storage formats, approval rules, plugin installation behavior, or terminal behavior.

## Assumptions And Open Questions

- Open questions: none. The user approved the prior text design direction and asked to write requirements and plan.
- Low-risk assumption: P0.9 implementation will use the existing approved project documentation locations, `.codecub/spec` and `.codecub/plan`.
- Low-risk assumption: this UI polish pass should optimize the desktop project session page first; welcome/settings may receive token consistency only if needed to avoid visual mismatch.

## Confirmed Scope

In scope:

- Project session page layout changes.
- CSS token system and visual palette.
- Restrained glass/translucent surfaces for toolbar, sidebars, status chip, and run trail.
- Chat message hierarchy polish.
- Project sidebar for project context, chat history, plugins, and skills.
- Run inspector for status, approvals, diff, run trail, and log.
- CodeCub status chip derived from existing `chatState.runStatus`.
- Run trail derived from existing backend events.
- Focus/hover/disabled/error states.
- Component tests and layout smoke tests.

Out of scope:

- Backend protocol changes.
- New event types.
- Pet growth, skins, affinity, feeding, or economy systems.
- Plugin marketplace or plugin runtime execution.
- Large decorative animations.
- Decorative glassmorphism effects that reduce readability.
- Displaying hidden reasoning or chain-of-thought.
- Full responsive mobile design; this remains a desktop app with current minimum window constraints.

## Files And Responsibilities

- Modify `desktop/src/styles/app.css`: add design tokens, three-column layout, component styling, focus states, and reduced-motion-safe transitions.
- Modify `desktop/src/components/ProjectSessionPage.tsx`: reorganize project session UI into left/center/right regions.
- Create `desktop/src/components/ProjectSidebar.tsx`: render project context, chat history, and extension panels.
- Create `desktop/src/components/RunInspectorPanel.tsx`: render CodeCub status chip, approval area, run trail, diff preview, and run log.
- Create `desktop/src/components/CubStatusChip.tsx`: compact status display from chat state.
- Create `desktop/src/components/RunTrail.tsx`: visual progress summary derived from events and run status.
- Create `desktop/src/state/runTrailState.ts`: pure function for mapping backend events/chat state to run trail steps.
- Modify `desktop/src/components/ChatView.tsx`: improve message hierarchy and keep streaming status visible.
- Modify `desktop/src/components/Toolbar.tsx`: add compact model/project-oriented visual treatment only if existing props support it; otherwise keep behavior unchanged.
- Modify `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`: add new labels.
- Add tests under `desktop/tests`.

## Task 1: Add UI Token Baseline

**Files:**
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/uiTokens.test.ts`

- [ ] **Step 1: Write token existence test**

Create `desktop/tests/uiTokens.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("UI tokens", () => {
  it("defines the P0.9 Quiet Cub Workbench token baseline", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("--color-bg: #F6F8FA");
    expect(css).toContain("--color-brand: #2F6F73");
    expect(css).toContain("--color-accent: #D6A84F");
    expect(css).toContain("--color-code-surface: #111820");
    expect(css).toContain("--color-glass-surface: rgba(255, 255, 255, 0.72)");
    expect(css).toContain("--color-glass-border: rgba(217, 224, 231, 0.72)");
    expect(css).toContain("--radius-panel: 8px");
    expect(css).toContain("--font-code:");
  });
});
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
cd desktop
npm run test -- uiTokens.test.ts
```

Expected: FAIL because tokens do not exist yet.

- [ ] **Step 3: Add CSS tokens**

In `desktop/src/styles/app.css`, replace the current `:root` block with tokenized values:

```css
:root {
  --font-ui: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-code: "JetBrains Mono", Consolas, "SFMono-Regular", monospace;
  --color-bg: #F6F8FA;
  --color-surface: #FFFFFF;
  --color-surface-subtle: #EEF2F5;
  --color-text: #17202A;
  --color-muted: #64717F;
  --color-border: #D9E0E7;
  --color-brand: #2F6F73;
  --color-accent: #D6A84F;
  --color-danger: #B42318;
  --color-success: #1F7A4D;
  --color-warning: #9A5A00;
  --color-code-surface: #111820;
  --color-glass-surface: rgba(255, 255, 255, 0.72);
  --color-glass-border: rgba(217, 224, 231, 0.72);
  --blur-glass: blur(14px);
  --radius-panel: 8px;
  --radius-control: 7px;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  font-family: var(--font-ui);
  color: var(--color-text);
  background: var(--color-bg);
}
```

Then replace obvious repeated colors in the edited sections with tokens without changing component behavior. Apply translucent surfaces only to toolbar, sidebars, status chip, and run trail containers. Keep terminal, code/diff blocks, error banners, and message content surfaces fully readable.

- [ ] **Step 4: Run token test**

Run:

```powershell
npm run test -- uiTokens.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add desktop/src/styles/app.css desktop/tests/uiTokens.test.ts
git commit -m "Add Quiet Cub UI tokens"
```

## Task 2: Add Run Trail State Mapping

**Files:**
- Create: `desktop/src/state/runTrailState.ts`
- Test: `desktop/tests/runTrailState.test.ts`

- [ ] **Step 1: Write state tests**

Create `desktop/tests/runTrailState.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { deriveRunTrail, type RunTrailStepId } from "../src/state/runTrailState";
import type { BackendEvent } from "../src/state/backendEvents";
import type { ChatState } from "../src/state/chatState";

function chat(phase: string): ChatState {
  return {
    messages: [],
    activeRunId: "r1",
    isRunning: true,
    runStatus: {
      runId: "r1",
      phase,
      label: phase,
      detail: "",
      startedAt: "",
      elapsedMs: 0,
      updatedAt: "2026-06-16T00:00:00Z",
    },
  };
}

function event(type: BackendEvent["type"]): BackendEvent {
  return { type, timestamp: "2026-06-16T00:00:00Z", session_id: "s1", run_id: "r1", payload: {} };
}

describe("deriveRunTrail", () => {
  it("marks context and model steps from run status", () => {
    const trail = deriveRunTrail(chat("model_streaming"), []);
    expect(activeIds(trail)).toEqual(["context", "model"]);
  });

  it("marks tool and diff steps from existing events", () => {
    const trail = deriveRunTrail(chat("tool_running"), [event("tool_started"), event("diff_summary")]);
    expect(activeIds(trail)).toEqual(["context", "model", "tool", "diff"]);
  });

  it("marks done when the run completes", () => {
    const trail = deriveRunTrail({ ...chat("completed"), isRunning: false }, [event("run_completed")]);
    expect(activeIds(trail)).toEqual(["context", "model", "done"]);
  });
});

function activeIds(trail: ReturnType<typeof deriveRunTrail>): RunTrailStepId[] {
  return trail.filter((step) => step.state !== "pending").map((step) => step.id);
}
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
npm run test -- runTrailState.test.ts
```

Expected: FAIL because `runTrailState.ts` does not exist.

- [ ] **Step 3: Implement pure mapper**

Create `desktop/src/state/runTrailState.ts`:

```ts
import type { BackendEvent } from "./backendEvents";
import type { ChatState } from "./chatState";

export type RunTrailStepId = "context" | "model" | "tool" | "diff" | "done";
export type RunTrailStepState = "pending" | "active" | "complete";
export type RunTrailStep = { id: RunTrailStepId; state: RunTrailStepState };

const steps: RunTrailStepId[] = ["context", "model", "tool", "diff", "done"];

export function deriveRunTrail(chatState: ChatState, events: BackendEvent[]): RunTrailStep[] {
  const active = new Set<RunTrailStepId>();
  const phase = chatState.runStatus?.phase ?? "";

  if (chatState.isRunning || phase || events.length > 0) {
    active.add("context");
  }
  if (phase.includes("model") || phase === "finalizing" || hasEvent(events, "assistant_delta") || hasEvent(events, "assistant_message")) {
    active.add("context");
    active.add("model");
  }
  if (phase.includes("tool") || hasEvent(events, "tool_started") || hasEvent(events, "tool_result")) {
    active.add("context");
    active.add("model");
    active.add("tool");
  }
  if (hasEvent(events, "diff_summary")) {
    active.add("context");
    active.add("model");
    active.add("tool");
    active.add("diff");
  }
  if (hasEvent(events, "run_completed") || phase === "completed") {
    active.add("context");
    active.add("model");
    active.add("done");
  }

  const current = [...active].at(-1);
  return steps.map((id) => ({
    id,
    state: !active.has(id) ? "pending" : id === current && chatState.isRunning ? "active" : "complete",
  }));
}

function hasEvent(events: BackendEvent[], type: BackendEvent["type"]): boolean {
  return events.some((event) => event.type === type);
}
```

- [ ] **Step 4: Run state test**

Run:

```powershell
npm run test -- runTrailState.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add desktop/src/state/runTrailState.ts desktop/tests/runTrailState.test.ts
git commit -m "Add run trail state mapping"
```

## Task 3: Add Cub Status Chip And Run Trail Components

**Files:**
- Create: `desktop/src/components/CubStatusChip.tsx`
- Create: `desktop/src/components/RunTrail.tsx`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`
- Test: `desktop/tests/CubStatusChip.test.tsx`
- Test: `desktop/tests/RunTrail.test.tsx`

- [ ] **Step 1: Add component tests**

Create `desktop/tests/CubStatusChip.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CubStatusChip } from "../src/components/CubStatusChip";
import { t } from "../src/i18n";
import type { ChatState } from "../src/state/chatState";

function state(phase: string, label = phase): ChatState {
  return {
    messages: [],
    activeRunId: "r1",
    isRunning: Boolean(phase),
    runStatus: phase
      ? { runId: "r1", phase, label, detail: "", startedAt: "", elapsedMs: 1200, updatedAt: "" }
      : null,
  };
}

describe("CubStatusChip", () => {
  it("shows ready when no run is active", () => {
    render(<CubStatusChip t={(key) => t("en-US", key)} chatState={state("")} />);
    expect(screen.getByText("Ready")).toBeTruthy();
  });

  it("shows safe observable run status and elapsed time", () => {
    render(<CubStatusChip t={(key) => t("en-US", key)} chatState={state("model_streaming", "Receiving model response")} />);
    expect(screen.getByText("Receiving model response")).toBeTruthy();
    expect(screen.getByText("00:01")).toBeTruthy();
  });
});
```

Create `desktop/tests/RunTrail.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunTrail } from "../src/components/RunTrail";
import { t } from "../src/i18n";

describe("RunTrail", () => {
  it("renders every high-level run step", () => {
    render(
      <RunTrail
        t={(key) => t("en-US", key)}
        steps={[
          { id: "context", state: "complete" },
          { id: "model", state: "active" },
          { id: "tool", state: "pending" },
          { id: "diff", state: "pending" },
          { id: "done", state: "pending" },
        ]}
      />,
    );
    expect(screen.getByText("Context")).toBeTruthy();
    expect(screen.getByText("Model")).toBeTruthy();
    expect(screen.getByText("Tool")).toBeTruthy();
    expect(screen.getByText("Diff")).toBeTruthy();
    expect(screen.getByText("Done")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run failing component tests**

Run:

```powershell
npm run test -- CubStatusChip.test.tsx RunTrail.test.tsx
```

Expected: FAIL because components and i18n keys do not exist.

- [ ] **Step 3: Implement components and i18n**

Create `CubStatusChip.tsx` with a compact `section`/`div` that renders brand mark, status label, detail when present, and `mm:ss` elapsed time. Use only `chatState.runStatus.label`, `detail`, and `elapsedMs`.

Create `RunTrail.tsx` that renders each step as a small pill with classes:

```text
run-trail-step pending
run-trail-step active
run-trail-step complete
```

Add i18n keys:

```ts
cubStatus: "CodeCub 状态" / "CodeCub Status"
trailContext: "上下文" / "Context"
trailModel: "模型" / "Model"
trailTool: "工具" / "Tool"
trailDiff: "变更" / "Diff"
trailDone: "完成" / "Done"
```

- [ ] **Step 4: Add CSS**

Add styles to `app.css` for:

```text
.cub-status-chip
.cub-status-mark
.cub-status-main
.cub-status-label
.cub-status-detail
.cub-status-time
.run-trail
.run-trail-step
.run-trail-step.active
.run-trail-step.complete
```

The chip must stay compact and not exceed one row in the right inspector header.
Use `--color-glass-surface`, `--color-glass-border`, and `--blur-glass` for the chip background, with a solid readable fallback color.

- [ ] **Step 5: Run component tests**

Run:

```powershell
npm run test -- CubStatusChip.test.tsx RunTrail.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add desktop/src/components/CubStatusChip.tsx desktop/src/components/RunTrail.tsx desktop/src/i18n/zh-CN.ts desktop/src/i18n/en-US.ts desktop/src/styles/app.css desktop/tests/CubStatusChip.test.tsx desktop/tests/RunTrail.test.tsx
git commit -m "Add Cub status chip and run trail"
```

## Task 4: Split Project Session Into Three Columns

**Files:**
- Create: `desktop/src/components/ProjectSidebar.tsx`
- Create: `desktop/src/components/RunInspectorPanel.tsx`
- Modify: `desktop/src/components/ProjectSessionPage.tsx`
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/ProjectSessionPageLayout.test.tsx`

- [ ] **Step 1: Write layout smoke test**

Create `desktop/tests/ProjectSessionPageLayout.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectSessionPage } from "../src/components/ProjectSessionPage";
import { t } from "../src/i18n";
import { createInitialApprovalState } from "../src/state/approvalState";
import { createInitialChatState } from "../src/state/chatState";

describe("ProjectSessionPage layout", () => {
  it("renders project sidebar, center workbench, and run inspector", () => {
    render(
      <ProjectSessionPage
        t={(key) => t("en-US", key)}
        projectPath="D:/repo"
        events={[]}
        chatState={createInitialChatState()}
        approvalState={createInitialApprovalState()}
        projectSessions={[]}
        sessionError=""
        extensions={{ skills: [], plugins: [] }}
        extensionError=""
        backendError=""
        onSend={vi.fn()}
        onStop={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onImportLegacy={vi.fn()}
        onRefreshSessions={vi.fn()}
        onResumeSession={vi.fn()}
        onRefreshExtensions={vi.fn()}
        onInstallSkill={vi.fn()}
        onInstallPlugin={vi.fn()}
        onSettings={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Project context")).toBeTruthy();
    expect(screen.getByLabelText("Workbench")).toBeTruthy();
    expect(screen.getByLabelText("Run inspector")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run failing layout test**

Run:

```powershell
npm run test -- ProjectSessionPageLayout.test.tsx
```

Expected: FAIL because the labelled regions do not exist.

- [ ] **Step 3: Create `ProjectSidebar.tsx`**

Move `ProjectHistoryPanel` and `ExtensionsPanel` into this component. Render project path and `GitStatusBadge` at the top. Keep props purely presentational.

- [ ] **Step 4: Create `RunInspectorPanel.tsx`**

Render:

```tsx
<CubStatusChip />
<RunTrail />
<ApprovalDialog />
<DiffPreviewPanel />
<RunLogSidebar />
```

Keep approval actions passed through from `ProjectSessionPage`.

- [ ] **Step 5: Update `ProjectSessionPage.tsx`**

Use this structure:

```tsx
<div className="workspace-layout workspace-layout-polished">
  <ProjectSidebar ... />
  <main className="workspace-main" aria-label={t("workbench")}>...</main>
  <RunInspectorPanel ... />
</div>
```

Remove `GitStatusBadge`, `ProjectHistoryPanel`, `ExtensionsPanel`, `ApprovalDialog`, `DiffPreviewPanel`, and `RunLogSidebar` direct rendering from `ProjectSessionPage` after moving them into the new components.

- [ ] **Step 6: Update CSS layout**

Change `.workspace-layout` to:

```css
.workspace-layout {
  flex: 1;
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 360px;
  min-height: 0;
}
```

Add:

```text
.project-sidebar
.workspace-main
.run-inspector
.sidebar-project-card
.inspector-section
```

Make each column independently scrollable only where needed. Do not create nested cards inside cards.
Use translucent column surfaces only at the top-level sidebar/inspector shell. Inner content should remain quiet and readable, avoiding stacked glass cards.

- [ ] **Step 7: Run layout test**

Run:

```powershell
npm run test -- ProjectSessionPageLayout.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add desktop/src/components/ProjectSidebar.tsx desktop/src/components/RunInspectorPanel.tsx desktop/src/components/ProjectSessionPage.tsx desktop/src/styles/app.css desktop/tests/ProjectSessionPageLayout.test.tsx
git commit -m "Split project session into three-column workbench"
```

## Task 5: Polish Chat Hierarchy

**Files:**
- Modify: `desktop/src/components/ChatView.tsx`
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/ChatViewLayout.test.tsx`
- Existing Test: `desktop/tests/ChatViewRunStatus.test.tsx`

- [ ] **Step 1: Write chat hierarchy test**

Create `desktop/tests/ChatViewLayout.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "../src/components/ChatView";
import { t } from "../src/i18n";
import type { ChatState } from "../src/state/chatState";

describe("ChatView layout", () => {
  it("keeps user and assistant messages visually distinguishable", () => {
    const chatState: ChatState = {
      activeRunId: "",
      isRunning: false,
      runStatus: null,
      messages: [
        { id: "u1", role: "user", content: "Change the UI", runId: "r1", createdAt: "" },
        { id: "a1", role: "assistant", content: "I will update the layout.", runId: "r1", createdAt: "" },
      ],
    };

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.getByText("Change the UI").closest(".message")).toHaveClass("user");
    expect(screen.getByText("I will update the layout.").closest(".message")).toHaveClass("assistant");
  });
});
```

- [ ] **Step 2: Run chat tests**

Run:

```powershell
npm run test -- ChatViewLayout.test.tsx ChatViewRunStatus.test.tsx
```

Expected: current test may pass or fail depending on existing class names; keep it as a regression harness.

- [ ] **Step 3: Update message styling**

Adjust CSS:

- Assistant messages: wider, left-aligned, white surface, subtle border.
- User messages: narrower, right-aligned, brand-tinted subtle background.
- Message roles: consistent 11-12px metadata styling.
- Composer: stronger separation, stable button sizing.

Do not change message data shape.

- [ ] **Step 4: Run chat tests**

Run:

```powershell
npm run test -- ChatViewLayout.test.tsx ChatViewRunStatus.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add desktop/src/components/ChatView.tsx desktop/src/styles/app.css desktop/tests/ChatViewLayout.test.tsx
git commit -m "Polish chat message hierarchy"
```

## Task 6: Final UI Verification

**Files:**
- Possibly Modify: `desktop/tests/AppRecentProject.test.tsx` if layout role changes require mock updates.

- [ ] **Step 1: Run targeted UI tests**

Run:

```powershell
cd desktop
npm run test -- uiTokens.test.ts runTrailState.test.ts CubStatusChip.test.tsx RunTrail.test.tsx ProjectSessionPageLayout.test.tsx ChatViewLayout.test.tsx ChatViewRunStatus.test.tsx
```

Expected: PASS.

- [ ] **Step 2: Run full desktop tests**

Run:

```powershell
npm run test
```

Expected: PASS.

- [ ] **Step 3: Run typecheck**

Run:

```powershell
npm run typecheck
```

Expected: PASS.

- [ ] **Step 4: Run production build**

Run:

```powershell
npm run build
```

Expected: PASS.

- [ ] **Step 5: Manual app smoke**

Run the desktop app through the existing dev flow or packaged app available in the repo. Verify:

- Project page shows three columns.
- Left column contains project context, chat history, plugins, and skills.
- Center column contains chat, composer, and terminal.
- Right column contains CodeCub status, run trail, approvals, diff, and run log.
- Backend startup failure still allows project page and terminal access.
- No visible text overlap at the current desktop minimum size.
- Translucent surfaces are visible but do not reduce text, diff, terminal, or error readability.
- No hidden reasoning or chain-of-thought is displayed.

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short
```

Expected: only intended P0.9 files are changed, plus any pre-existing unrelated `desktop/index.html` state.

## Plan Self-Review

- Spec coverage: The plan covers three-column workbench, visual tokens, restrained translucent surfaces, chat hierarchy, CodeCub status chip, run trail, i18n, and verification.
- Existing behavior preservation: No task changes backend, IPC, storage, model providers, approval policy, terminal behavior, or extension installation logic.
- Placeholder scan: No task uses undefined placeholders. Each task identifies target files and concrete verification commands.
- Type consistency: `RunTrailStepId`, `RunTrailStepState`, `RunTrailStep`, `deriveRunTrail`, `CubStatusChip`, and `RunTrail` are introduced before use in later tasks.
- Maintainability: Project layout is split into focused presentational components instead of expanding `ProjectSessionPage.tsx` further.
- Risk: CSS layout changes can break existing app tests; Task 6 requires full desktop tests, typecheck, build, and manual smoke. The pre-existing dirty `desktop/index.html` must not be reverted or included unless explicitly requested.
