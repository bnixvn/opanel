# Shared BNIX interface

The stylesheets and fonts that OPanel and BPanel both load, so the two panels
look the same. OPanel is the reference: change these files in OPanel only,
then run, from OPanel's `frontend/`:

    node scripts/sync-shared-ui.mjs <path-to-bpanel-checkout>

It rewrites `MANIFEST` and copies this folder into BPanel. In each repo,
`backend/app/tests/test_shared_ui.py` fails when a file here does not match
`MANIFEST` -- an edit made in one repo alone.

Rules for one product only go in that product's own stylesheet (BPanel:
`src/bpanel.css`), never here. The two stay separate products: separate
repos, backends and releases; only this layer is common.
