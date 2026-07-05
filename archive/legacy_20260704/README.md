# Legacy Archive 2026-07-04

This archive keeps files that are no longer part of the active runtime layout
but may still be useful for demos, comparison, or rollback.

## Contents

- `dashboard_preview/xinyu_preview.html`
- `dashboard_preview/xinyu_preview.css`
- `dashboard_preview/xinyu_preview.js`
- `dashboard_preview/page2_preview/`
- `dashboard_preview/ui_preview_responsive.png`
- `core_orphans/control_filter.py`

## Original Locations

- `dashboard/xinyu_preview.*`
- `dashboard/page2_preview/`
- `dashboard/ui_preview_responsive.png`
- `core/control_filter.py`

## Restore Notes

To temporarily serve the old recording preview again, copy the preview files
back to their original `dashboard/` paths and restart FastAPI. The archived
preview HTML still contains its original `/static/page2_preview/...` references,
so the `page2_preview/` directory must be restored together with
`xinyu_preview.*`.

`core_orphans/control_filter.py` was moved because no production code imports
it. Keep it archived unless a future control refactor explicitly reintroduces
it through the single `main_phase3.py` control plane.
