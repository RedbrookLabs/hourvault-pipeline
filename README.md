# Hour Vault requirements pipeline

The daily recheck for Hour Vault's state apprenticeship requirements. Same mechanism as CE Compass's
`cecompass-pipeline`: a GitHub Actions job downloads each state's official page, confirms every number with a
pattern from `rules/checks.yml`, and publishes the result. Free tier only, no secrets.

| Path | What it is |
| --- | --- |
| `rules/requirements.json` | Source of truth: one template per state (OJT target, classroom target, notes, official source URL) plus default categories per trade. Edit this to add a state. |
| `rules/checks.yml` | For each template: the official pages and one regex per number. |
| `scraper/verify.py` | The check. Writes `rules/status.json`, `docs/requirements.json` and `docs/manifest.json`. |
| `.github/workflows/recheck.yml` | Runs daily at 11:41 UTC, on demand, and on every change to the rules. Opens an issue when a state loses its verified status. |
| `docs/` | Served by GitHub Pages at `https://redbrooklabs.github.io/hourvault-pipeline/`. The app reads `manifest.json` daily and downloads `requirements.json` only when its sha256 changes. Also holds a copy of the Hour Vault website; the live site is the Cloudflare Pages project `hourvault` at https://hourvault.redbrooklabs.com (deploy with `_publish/deploy-hourvault.command`). |

## One-time setup (done for you if the repo exists)
1. Create the public repo `RedbrookLabs/hourvault-pipeline` and push this folder. (Public keeps Actions minutes and Pages free.)
2. Settings > Pages > Source: **Deploy from a branch**, Branch **main**, folder **/docs**.
3. Actions tab > enable workflows > **Recheck apprenticeship requirements** > **Run workflow**.

## Adding a state
Add its template to `rules/requirements.json` (official source only: board, statute, or state DOL page) and a
matching entry in `rules/checks.yml`. Push. Within a minute the run page says whether every number matched. A
state whose numbers can't be confirmed stays `needs_review` and the app shows it as unconfirmed.

## Run it locally
```
pip install -r scraper/requirements.txt
python scraper/verify.py
```
The readable text of every source page is saved in `rules/source-text/` to help write patterns.
