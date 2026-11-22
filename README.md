# TilikCare

**Find. Verify. Better.**

Tell us where the patient is and what care they need. TilikCare finds the nearest facilities in India that offer it, and shows the evidence from each facility's own record so you can judge it before you call.

Built on Databricks Free Edition for the CMU DSC x Databricks x Virtue Foundation datathon (Fall 2026). Pilot focus: Gaya District, Bihar.

## Channels
- **Web app** (`app/`): landing page plus a map dashboard, deployed as a Databricks App. Search bar ("What care" + "Where"), result cards, map with distance pins coloured by evidence, and a facility page with road distance, an evidence checklist and directions.
- **SMS** (`relay/`): a health worker texts `CARE <need> <PIN or city>` from any basic phone. A small Python relay passes it to the same Databricks functions and replies by SMS with the 2 nearest options.

Both channels use the same SQL function (`find_care_fast`) and the same evidence score, so they return the same facilities.

## How a search works
1. **What is needed:** the need is matched to one of 6 services (Dialysis, Emergency C-section, NICU, Cardiac surgery, MRI, Burn care): exact name or synonym first, then embedding similarity, then an LLM (Llama 3.3 70B, temperature 0). If the match is not certain, the user is asked to confirm ("Did you mean X?").
2. **From where:** a PIN code, city, pasted coordinates, a dropped map pin or "Nearby" (browser location) gives the patient's location.
3. **Which facilities:** facilities whose record mentions the service, minus those an AI check marked "not offered" or "planned", nearest first (20 on the web, 2 by SMS).
4. **How strong the evidence is:** a score from 0 to 4, shown as a checklist:
   - States it as a capability
   - Lists a matching procedure
   - Lists matching equipment
   - Mentioned in more than one part of the record

   Points are deducted for warnings (e.g. wording suggests the service is only planned). A link to the source page is not counted as evidence.
5. **Decide:** road distance and drive time (OpenStreetMap routing), call, website and Google Maps directions.

## Folders
| Folder | Contents |
|---|---|
| `app/` | Streamlit app: `app.py` (search and data), `landing.py` (landing page), `tilik_map/` (map dashboard component), `mock_backend.py` (offline demo data), `app.yaml`, requirements |
| `relay/` | SMS relay (`sms_relay_v4.py`, Python standard library only) |
| `sql/` | SQL that builds the derived tables and functions |
| `sql/live/` | Current definitions of the functions, exported from Unity Catalog (JSON) |

## SQL files
| File | Builds | Used live |
|---|---|---|
| `10_classifier_setup.sql` | `service_profiles`, `classifier_testset` | yes |
| `11_classify_service.sql` | `classify_service` function, `service_classifier_cache` | yes (SMS, web fallback) |
| `20_candidate_verification.sql` | `candidate_verification_test` (100-row test) | no |
| `21_candidate_verification_full.sql` | `candidate_verification` (AI check of every candidate) | yes |
| `30_fast_tables.sql` | stored copies (`*_tbl`) of the views the search reads | yes |
| `31_fast_functions.sql` | `resolve_location_fast`, `find_care_fast` | yes (web and SMS) |
| `01`-`03_*_v2.sql` | wider-keyword v2 engine | no (kept for reference) |

The stored copies make a search take about 2 seconds instead of 8 to 12. Rebuild them with `30_fast_tables.sql` if the source data changes.

Not yet in the repo: the SQL that built the original base views (`facility_records`, `service_candidates`, `candidate_strength`, `pincode_reference`, `city_origins`, ...).

## Running
**Web app**
```
cd app
databricks sync . /Workspace/Users/<you>/tilikcare-app
databricks apps deploy tilikcare --source-code-path /Workspace/Users/<you>/tilikcare-app
```
The app's service principal needs `USE CATALOG`, `USE SCHEMA`, `SELECT` and `EXECUTE` on the schema.

Offline demo with invented facilities (no Databricks needed):
```
cd app
CAREMATCH_MOCK=1 python -m streamlit run app.py
```
Optional: set `ORS_API_KEY` for OpenRouteService routing (otherwise the public OSRM demo server is used).

**SMS relay**
Set `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_WAREHOUSE_ID`, `SMSGATE_URL`, `SMSGATE_USER` and `SMSGATE_PASS` as environment variables, then:
```
python3 relay/sms_relay_v4.py
```
The relay listens on port 8787. It needs a public HTTPS address (e.g. `cloudflared tunnel --url http://127.0.0.1:8787`) registered as an `sms:received` webhook on an Android phone running SMS Gateway for Android.

Secrets are never stored in this repo.

## Limits
- Evidence is the facility's own web-scraped text, not verified availability. Users are always told to call first.
- Only 6 services are supported.
- Results are ranked by straight-line distance; road distance is shown for the selected facility only.
- Does not diagnose. In an emergency, call 108.
