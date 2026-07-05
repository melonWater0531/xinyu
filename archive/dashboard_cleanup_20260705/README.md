# Dashboard Cleanup Archive - 2026-07-05

This archive keeps dashboard files that are no longer part of the active
`dashboard/` surface after the product Home migration.

## Served backup

- `home_legacy/` contains the previous Home page and its self-care assets.
- FastAPI serves the page at `/home-old`.
- FastAPI serves its static files at `/home-old-static/*`.

## Dormant assets

- `unused_dashboard_assets/` contains the old island SVG/GLB and local Three.js
  vendor copy.
- These files are not referenced by the active `/home` product page or the
  `/control` dashboard.
- Keep them here only as rollback/reference material.

## Active dashboard layout

The active `dashboard/` directory is intentionally limited to:

- `home.html` and `product_home/` for `/home` and `/`.
- `recamera_v2_live.html` and `tracking_overlay.js` for `/control` and `/v2`.
- `manifest.webmanifest`, `sw.js`, and `icons/` for PWA support.
