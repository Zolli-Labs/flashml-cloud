# Zolli Frontend Brand Asset Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical Zolli v3 pack the deployed visual identity throughout the frontend.

**Architecture:** Curated static assets live under Next.js `public/brand/` and are referenced through stable URLs. Existing `Mark`, `Wordmark`, and `ZolliCharacter` APIs become image-backed adapters so all current call sites inherit the new identity with minimal churn. Root metadata owns browser, social, and PWA presentation.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Next Image, Vitest, Tailwind CSS 4.

## Global Constraints

- This phase is visual-only; do not change authentication, API, database, or runtime behavior.
- Use only canonical files from `/Users/phongcao/Downloads/zolli_brand_asset_pack_v3`.
- Deploy assets with the app from `flashml-cloud/apps/web/public/brand/`; no external storage.
- Preserve existing component APIs and reduced-motion behavior.
- Do not deploy alternates, contact sheets, pose guides, prompts, or reference boards.

---

### Task 1: Curated runtime asset library

**Files:**
- Create: `flashml-cloud/apps/web/public/brand/logos/*.png`
- Create: `flashml-cloud/apps/web/public/brand/icons/*.png`
- Create: `flashml-cloud/apps/web/public/brand/social/*.png`
- Create: `flashml-cloud/apps/web/public/brand/characters/*.png`
- Create: `flashml-cloud/apps/web/public/brand/brand-tokens.json`
- Replace: `flashml-cloud/apps/web/app/icon.png`
- Replace: `flashml-cloud/apps/web/app/apple-icon.png`

**Interfaces:**
- Consumes: canonical v3 pack filenames.
- Produces: stable `/brand/logos/`, `/brand/icons/`, `/brand/social/`, and `/brand/characters/` URLs.

- [x] **Step 1: Copy only the approved runtime files**

Copy canonical logos, all production icons, both social images, six standalone characters, and `brand-tokens.json` without modification.

- [x] **Step 2: Synchronize Next metadata icons**

Replace `app/icon.png` with `icons/favicon-128.png` and `app/apple-icon.png` with `icons/apple-touch-icon-180.png`.

- [x] **Step 3: Verify the asset inventory**

Run:

```bash
find flashml-cloud/apps/web/public/brand -type f | sort
file flashml-cloud/apps/web/public/brand/{logos,icons,social,characters}/*.png
```

Expected: only the approved production set exists, and all image files are valid PNGs.

### Task 2: Image-backed brand component contracts

**Files:**
- Modify: `flashml-cloud/apps/web/lib/zolli-brand.test.ts`
- Modify: `flashml-cloud/apps/web/components/brand/Mark.tsx`
- Modify: `flashml-cloud/apps/web/components/brand/ZolliCharacter.tsx`

**Interfaces:**
- Consumes: `/brand/logos/logo-primary.png`, `/brand/logos/logo-symbol-orange.png`, and `/brand/characters/<role>.png`.
- Produces: compatible `Mark`, `Wordmark`, and `ZolliCharacter` React components.

- [x] **Step 1: Write failing component contract tests**

Assert that the mark renders `/brand/logos/logo-symbol-orange.png`, the wordmark renders `/brand/logos/logo-primary.png`, each role maps to its matching `/brand/characters/<role>.png`, labelled characters remain exposed, decorative characters remain hidden, and animated characters retain motion-safe classes.

- [x] **Step 2: Run the targeted test and verify RED**

Run:

```bash
npm test -- lib/zolli-brand.test.ts
```

Expected: FAIL because the current components render generated SVG paths instead of canonical image URLs.

- [x] **Step 3: Implement the image-backed adapters**

Use `next/image` with explicit intrinsic dimensions, preserve caller-controlled display size and `className`, and retain the existing `product` and accessibility behavior.

- [x] **Step 4: Run the targeted test and verify GREEN**

Run:

```bash
npm test -- lib/zolli-brand.test.ts
```

Expected: PASS.

### Task 3: Shared logo adoption and metadata

**Files:**
- Modify: `flashml-cloud/apps/web/components/auth/AuthShell.tsx`
- Modify: `flashml-cloud/apps/web/app/layout.tsx`
- Create: `flashml-cloud/apps/web/app/manifest.ts`

**Interfaces:**
- Consumes: shared `Wordmark` component and stable `/brand/` URLs.
- Produces: canonical auth branding, icon metadata, social previews, and install metadata.

- [x] **Step 1: Replace the auth-local wordmark**

Remove `AuthWordmark`; render the shared `Wordmark` at desktop and compact sizes.

- [x] **Step 2: Declare metadata images**

Add root `icons`, Open Graph image metadata, and Twitter card metadata using canonical `/brand/` paths.

- [x] **Step 3: Add the web manifest**

Return a `MetadataRoute.Manifest` named `ZolliAI Cloud` with `#FFFDF3` background, `#FF7427` theme, standalone display, and the 192px and 512px canonical icons.

### Task 4: Verification and documentation closeout

**Files:**
- Modify: `docs/superpowers/plans/2026-08-05-zolli-brand-assets.md`

**Interfaces:**
- Consumes: all implementation changes.
- Produces: verified frontend brand integration and checked plan status.

- [x] **Step 1: Run targeted and full automated checks**

```bash
npm test -- lib/zolli-brand.test.ts
npm test
npm run lint
npm run build
```

Expected: every command exits 0.

- [x] **Step 2: Inspect the running frontend**

Verify the landing page and sign-in page at desktop and mobile widths, including logo sharpness, mascot cropping, accessible names, and absence of layout shift.

- [x] **Step 3: Review the final diff**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; unrelated dirty files remain untouched.

- [x] **Step 4: Mark completed checklist items**

Change only completed plan steps from `- [ ]` to `- [x]` so the document reflects verified reality.
