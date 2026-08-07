# Zolli Frontend Brand Asset Integration

**Date:** 2026-08-05

## Goal

Make the canonical Zolli v3 brand pack the source of visible identity across the FlashML Cloud frontend while keeping this phase visual-only.

## Approved direction

Use deploy-with-app assets under `flashml-cloud/apps/web/public/brand/`. Do not introduce Supabase Storage, another CDN, or runtime asset fetching.

## Runtime asset set

- Canonical primary, reversed, monochrome, and symbol logos.
- Browser, Apple, Android/PWA, and app icons.
- Open Graph and GitHub social-preview images.
- Captain, Worker, Scout, Keeper, Relay, and Builder transparent PNGs.
- `brand-tokens.json` as the portable color and usage reference.

Reference boards, contact sheets, alternates, pose guides, prompts, and generation documentation stay out of the deployed frontend.

## Component design

1. Replace the hand-built mark and text wordmark with image-backed `Mark` and `Wordmark` exports so existing callers keep working.
2. Keep `Wordmark`'s `product` option and render “Cloud” as adjacent interface text; the canonical logo artwork itself remains unmodified.
3. Replace the generated SVG mascot body in `ZolliCharacter` with the matching official transparent PNG.
4. Preserve `ZolliCharacter`'s public props (`role`, `size`, `mood`, `animated`, `className`, and `label`). `mood` remains accepted for compatibility; motion applies to the image container.
5. Replace the local authentication wordmark with the shared canonical `Wordmark`.

## Metadata design

- Declare favicon, Apple touch icon, and Open Graph/Twitter images from `/brand/` in root Next.js metadata.
- Keep framework-discovered `app/icon.png` and `app/apple-icon.png` synchronized with the canonical icon files.
- Add a web manifest using the canonical Android/PWA icons and Zolli theme colors.

## Accessibility and quality

- Decorative mascot images remain hidden from assistive technology.
- Labelled mascots expose `role="img"` and the supplied accessible label.
- Logos linked to home remain named by their surrounding link; raster logo content is decorative there.
- Every image preserves its original aspect ratio.
- Existing reduced-motion behavior remains intact.

## Non-goals

- No backend, authentication, CRM, protocol, or database rebrand.
- No new generated assets or edits to the supplied brand artwork.
- No external asset upload or asset-management service.

## Acceptance criteria

- All selected files are available below `/brand/` at stable paths.
- Navbar, console shell, auth shell, footer/closing CTA, and not-found branding use canonical artwork.
- All existing mascot placements resolve to official crew PNGs.
- Metadata references the canonical favicon, Apple/PWA icons, and Open Graph image.
- Targeted tests, lint, and production build pass.
