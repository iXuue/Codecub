# CodeCub P0.14 Product Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the CodeCub desktop frontend from a functional prototype to a polished product-feeling launcher and coding workbench.

**Architecture:** Keep the existing React/Electron state model and backend contracts. Apply the redesign through localized component markup improvements, stronger i18n copy, and a final CSS product-polish layer that can override earlier P0.9-P0.13 styling without rewriting the whole app.

**Tech Stack:** React 19, TypeScript, Electron, CSS tokens, GSAP micro-interactions, Vitest.

---

## Design Direction

Name: `CodeCub Command Deck`.

The app should feel like a calm desktop coding cockpit: precise, translucent, softly dimensional, but readable. The one distinctive element is a small "Cub core" identity mark and command-deck layout, not decorative blobs or toy-like pet UI.

Palette:

- Ink: `#0B1020`
- Panel: `rgba(255,255,255,0.78)`
- Mist: `#EEF6FB`
- Cub purple: `#9B7AE5`
- Electric cyan: `#38BDF8`
- Terminal navy: `#07111F`

Typography:

- UI/body: existing system stack.
- Data/code: existing JetBrains Mono/Consolas stack.
- Display treatment: heavier system weight with wider spacing only in small labels, not large negative letter spacing.

Layout:

```text
Welcome:
[brand command panel 34%] [recent project launch grid 66%]

Project:
[context rail 230px] [chat + composer + terminal flexible] [inspector 300px]
```

## Files

- Modify `desktop/src/components/WelcomePage.tsx`
  - Add a compact product hero, status chips, and richer recent project rows.

- Modify `desktop/src/components/Toolbar.tsx`
  - Fix mojibake window controls.
  - Add more polished toolbar semantics and compact action styling hooks.

- Modify `desktop/src/components/ProjectSidebar.tsx`
  - Improve project card structure with project name, path, and status hierarchy.

- Modify `desktop/src/components/ProjectSessionPage.tsx`
  - Improve the center workbench header and semantic grouping.

- Modify `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`
  - Add launch, workbench, and visual polish copy.

- Modify `desktop/src/styles/app.css`
  - Add final P0.14 product polish CSS layer.
  - Normalize spacing, borders, cards, toolbar, welcome, project sidebar, chat empty state, composer, terminal, and inspector.
  - Remove the visible "prototype shell" feel by making surfaces flush and deliberate.

- Modify tests as needed
  - Keep recent project click behavior.
  - Verify new welcome launcher text.
  - Verify project page landmarks still exist.

- Modify `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
  - Add P0.14 design acceptance requirements.

## Tasks

- [ ] Add P0.14 requirements to the spec.
- [ ] Rebuild welcome page markup around command deck layout.
- [ ] Fix toolbar mojibake controls and add stable CSS hooks.
- [ ] Refine project sidebar and project strip markup.
- [ ] Add i18n strings.
- [ ] Add P0.14 CSS override layer.
- [ ] Update tests.
- [ ] Run focused tests and typecheck.
- [ ] Run full tests.
- [ ] Build and package Windows app.
- [ ] Run packaged smoke.
- [ ] Review the resulting UI for text overlap, empty-state quality, terminal/log readability, and old unwanted status artifacts.

## Acceptance

- Home page should no longer look like a sparse prototype with a large empty right side.
- Recent projects should look clickable, organized, and product-grade.
- Project page should read as one integrated workbench rather than isolated bordered boxes.
- Toolbar window controls must not show mojibake.
- Chat empty state, composer, terminal dock, side panels, and run inspector must have consistent spacing and hierarchy.
- Text must remain crisp and readable in light theme.
- No backend/API behavior changes.
- Existing tests and package smoke must pass.
