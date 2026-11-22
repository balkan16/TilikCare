# CareMatch

Find the right care: a service need plus a location in India gives the nearest facilities that offer it, with the evidence quoted from each facility's own record. Built on Databricks Free Edition for the CMU DSC x Databricks x Virtue Foundation datathon (Fall 2026).

## Channels
- **Web app** (`app/`): Streamlit app deployed as a Databricks App.
- **SMS** (`relay/`): health worker texts `CARE <need> <PIN or city>`; a small Python relay on a laptop passes it to the same Databricks functions and replies by SMS.

Both channels call the same SQL function, `find_care`, so they return the same facilities.

## Folders
| Folder | Contents |
|---|---|
| `app/` | Streamlit app (`app.py`, `app.yaml`, map shapes, requirements) |
| `relay/` | SMS relay (`sms_relay_v4.py`, standard library only) |
| `sql/` | SQL that builds the derived tables and functions |
| `sql/live/` | Current definitions of every function, exported from Unity Catalog (JSON) |

## SQL files
| File | Builds | Used by the live app |
|---|---|---|
| `10_classifier_setup.sql` | `service_profiles`, `classifier_testset` | yes |
| `11_classify_service.sql` | `classify_service` function, `service_classifier_cache` | yes (SMS, web fallback) |
| `20_candidate_verification.sql` | `candidate_verification_test` (100-row test) | no |
| `21_candidate_verification_full.sql` | `candidate_verification` (AI check of every candidate) | yes, via `find_care` |
| `01`-`03_*_v2.sql` | wider-keyword v2 engine | no (kept for reference) |

Not yet in the repo: the SQL that built the original base tables (`facility_records`, `service_evidence`, `service_candidates`, `candidate_strength`, `pincode_reference`, `city_origins`, ...) and `candidate_corroboration`.

## How a search works
1. **What is needed:** name or synonym lookup, then embedding similarity, then an LLM (Llama 3.3 70B, temperature 0) maps the need to one of 6 services. If the LLM is only Medium sure, the user is asked to confirm.
2. **From where:** PIN code or city resolved to coordinates.
3. **Which facilities:** keyword candidates from the facility records, minus those the AI check marked not offered or planned, sorted by distance. Each result shows an evidence score (0-5) and an exact quote.

## Running
- App: `databricks apps deploy <app-name> --source-code-path /Workspace/Users/<you>/carematch-app`
- Relay: set `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_WAREHOUSE_ID`, `SMSGATE_URL`, `SMSGATE_USER`, `SMSGATE_PASS` as environment variables, then `python3 relay/sms_relay_v4.py`.

Secrets are never stored in this repo.

## Limits
- Evidence is the facility's own web-scraped text, not verified availability. Users are told to call first.
- Only 6 services are supported.
- Distance is straight-line, not road distance.
