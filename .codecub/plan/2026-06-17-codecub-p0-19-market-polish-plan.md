# CodeCub P0.19 Market Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CodeCub welcome page and project workbench feel cohesive, less cluttered, and closer to a polished desktop product.

**Architecture:** This pass is a visual refinement layer on top of the existing React/Electron structure. It keeps the current component boundaries and behavior, and concentrates layout, spacing, hierarchy, glass surfaces, and motion polish in `desktop/src/styles/app.css`.

**Tech Stack:** React, Electron, Vite, CSS tokens, GSAP micro-interactions already present in the app, Vitest for regression checks, CDP screenshots for manual visual verification.

---

## Current Visual Findings

- Welcome page: the split screen is too empty on the right, recent items are oversized, and the empty state looks like a gray placeholder block rather than an intentional dark glass surface.
- Project workbench: left sidebar, chat area, and right inspector all compete visually. The UI is functional but reads as stacked panels rather than one integrated workspace.
- Chat area: message bubbles are too wide and high-contrast, making the main workspace feel heavy. The activity stream sits awkwardly in the open canvas.
- Right inspector: run log cards are too card-heavy and repeated, making it feel busy even after heartbeat filtering.
- Terminal dock: the collapsed bar is useful, but it needs to read as a dock attached to the composer/workbench rather than a separate black block.

## Design Direction

- Product type: local AI coding desktop workbench.
- Audience: developers who need repeatable project work, readable logs, terminal access, and quick session recovery.
- Style: transparent dark/light glass, but with dense operational clarity instead of decorative cards.
- Signature element: a subtle "workbench rail" rhythm: left context, center conversation, right inspector share one continuous dark glass surface with tighter inner hierarchy.

Palette tokens:

- `--color-bg`: deep graphite / pale blue-gray by theme.
- `--color-surface`: translucent panel surface.
- `--color-surface-raised`: slightly stronger card/dock surface.
- `--color-text`: high contrast foreground.
- `--color-muted`: readable secondary text.
- `--color-accent-user`: user-selectable highlight.

## Files

- Modify: `desktop/src/styles/app.css`
  - Add P0.19 polish layer at the bottom so it overrides earlier historical CSS safely.
  - Refine welcome proportions, recent list density, empty state, workspace columns, message widths, activity stream alignment, terminal dock, side panels, and inspector cards.
- Modify: `desktop/tests/uiTokens.test.ts`
  - Add token/regression checks for the P0.19 polish layer.
- Modify: `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
  - Add P0.19 frontend market polish requirements and acceptance points.

## Tasks

### Task 1: Add P0.19 CSS Token and Layout Layer

**Files:**
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/uiTokens.test.ts`

- [ ] Add a `/* P0.19 market polish visual pass. */` section at the end of `app.css`.
- [ ] Define `--color-surface-raised`, `--color-panel-line`, `--shadow-soft`, `--workspace-gutter`, and `--message-measure` tokens for both dark and light themes.
- [ ] Update `.welcome.command-deck` proportions so the left rail is narrower and the right recent-project area uses a constrained max-width instead of floating in empty space.
- [ ] Restyle `.welcome-empty-state` to be transparent glass, not a flat gray block.
- [ ] Restyle `.recent-item` to be denser, with consistent 44px+ click targets, clearer hover/focus, and less unused vertical space.
- [ ] Add `uiTokens.test.ts` expectations for the P0.19 marker and new tokens.
- [ ] Run `npm run test -- uiTokens`.

### Task 2: Refine Project Workbench Density

**Files:**
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/ProjectSessionPageLayout.test.tsx`

- [ ] Refine `.workspace-layout` columns to reduce sidebar/inspector dominance while keeping each column independently scrollable.
- [ ] Make `.project-sidebar` and `.run-inspector` use the same surface rhythm and border treatment.
- [ ] Reduce duplicated card feeling in `.sidebar-project-card`, `.side-panel`, `.event-item`, and extension/history panels.
- [ ] Keep all left/right controls at least 32px visual height and 44px practical click area where possible.
- [ ] Run `npm run test -- ProjectSessionPageLayout`.

### Task 3: Improve Chat, Activity, Composer, and Terminal Hierarchy

**Files:**
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/ChatViewLayout.test.tsx`

- [ ] Center the message column with `--message-measure` so messages do not stretch across the whole workbench.
- [ ] Make user and assistant message bubbles less heavy and more readable in both themes.
- [ ] Align `.chat-activity-stream` with the message column and make the collapsed summary feel like part of the conversation flow.
- [ ] Make `.composer` and `.terminal-panel` read as a connected bottom dock with consistent radius, border, and shadow.
- [ ] Keep terminal expanded height stable and collapsed terminal bar compact.
- [ ] Run `npm run test -- ChatViewLayout ChatActivityStream TerminalPanel`.

### Task 4: Inspector and Log Readability Polish

**Files:**
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/RunLogSidebar.test.tsx`

- [ ] Restyle `.inspector-tabs` as a quieter segmented control.
- [ ] Make `.run-log-header` compact and sticky without looking like a separate card.
- [ ] Make `.event-item` cards denser, with better title/detail spacing and less visual weight.
- [ ] Preserve debug raw-event readability.
- [ ] Run `npm run test -- RunLogSidebar`.

### Task 5: Sync Requirements and Verify With Screenshots

**Files:**
- Modify: `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
- Test: full frontend and packaged smoke if CSS changes pass.

- [ ] Add P0.19 requirements covering market polish, reduced clutter, theme contrast, and screenshot-based verification.
- [ ] Run `npm run test`.
- [ ] Run `npm run typecheck`.
- [ ] Rebuild renderer/package if visual changes are satisfactory.
- [ ] Capture fresh welcome and project screenshots with the same CDP workflow.
- [ ] If screenshots still show obvious clutter, write a short follow-up repair checklist and implement it before final response.

## Plan Review

- Matches prior requirements: yes. It keeps the transparent dark/light theme direction, preserves CodeCub branding, and focuses on the clutter and product polish issues the user repeatedly reported.
- Unresolved ambiguity: none material. "Perfect" is subjective, so the practical acceptance bar is screenshot-based improvement in density, hierarchy, contrast, and consistency without breaking behavior.
- Maintenance risk: low. The plan avoids component rewrites and adds a final CSS override layer rather than deleting historical CSS. A later cleanup can consolidate the CSS file, but this pass prioritizes safe visual improvement.
