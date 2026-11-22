"""TilikCare: find care with evidence (Databricks App, Streamlit).

All matching, distance and evidence logic lives in Unity Catalog SQL functions in
workspace.carematch (suggest_services, resolve_location_v2, find_care). This app only
collects inputs, calls those functions, and presents the results.
"""

import html
import json
import os
import re

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from databricks import sql
from databricks.sdk.core import Config
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="TilikCare: Find Care with Evidence", layout="wide", initial_sidebar_state="collapsed")

SCHEMA = "workspace.carematch"
SERVICES = ["Dialysis", "Emergency C-section", "NICU", "Cardiac surgery", "MRI", "Burn care"]
SERVICE_REFS = {
    "Dialysis": ("MedlinePlus (US NIH)", "https://medlineplus.gov/dialysis.html"),
    "Emergency C-section": ("MedlinePlus (US NIH)", "https://medlineplus.gov/cesareandelivery.html"),
    "NICU": ("WHO standards for small and sick newborns", "https://www.who.int/publications/i/item/9789240010765"),
    "Cardiac surgery": ("MedlinePlus (US NIH)", "https://medlineplus.gov/heartsurgery.html"),
    "MRI": ("MedlinePlus (US NIH)", "https://medlineplus.gov/mriscans.html"),
    "Burn care": ("WHO fact sheet", "https://www.who.int/news-room/fact-sheets/detail/burns"),
}
INDIA_CENTER = (22.5, 79.0)
# Words that show an item is about the service (for highlighting evidence in the record's lists; display only)
SERVICE_TERMS = {
    "Dialysis": r"dialysis|dialyser|dialyzer|capd|nephrolog",  # not "kidney": "kidney stone treatment" is not dialysis
    "Emergency C-section": r"caesarean|cesarean|c-section|c section|lscs|obstetric",
    "NICU": r"nicu|neonat|newborn|sncu|incubator",
    "Cardiac surgery": r"cardiac surg|cabg|cardiothoracic|ctvs|open heart|bypass|valve|heart surgery|heart-lung",
    "MRI": r"\bmri\b|magnetic resonance|tesla",
    "Burn care": r"\bburns?\b",
}
# Keep every map on India: cannot pan outside this box or zoom out past the whole country.
INDIA_MAP = dict(min_zoom=4, max_bounds=True, min_lat=5.0, max_lat=38.0, min_lon=67.0, max_lon=98.5)
COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*$")

# --------------------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------------------
MOCK = os.getenv("CAREMATCH_MOCK") == "1"  # local UI work with invented sample data, no Databricks
cfg = None if MOCK else Config()  # picks up the app's service principal credentials automatically


@st.cache_resource(show_spinner=False)
def get_connection():
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    if not warehouse_id:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set. Add the SQL warehouse resource to the app.")
    return sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        credentials_provider=lambda: cfg.authenticate,
    )


def run_query(query: str, params: dict | None = None) -> pd.DataFrame:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            return cur.fetchall_arrow().to_pandas()
    except Exception:
        # Connection may have gone stale; reconnect once.
        get_connection.clear()
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            return cur.fetchall_arrow().to_pandas()


if MOCK:
    from mock_backend import run_query  # noqa: F811  (replaces the Databricks version above)


@st.cache_data(ttl=3600, show_spinner=False)
def location_options() -> list[str]:
    df = run_query(
        f"""
        SELECT label FROM (
          SELECT concat('City | ', city, ' (', state, ')') AS label, 1 AS ord FROM {SCHEMA}.city_origins
          UNION ALL
          SELECT concat('PIN ', postcode, ' | ', initcap(array_join(districts, '/')), ' (', initcap(array_join(states, '/')), ')'), 2
          FROM {SCHEMA}.pincode_reference_tbl
          WHERE approximate_latitude BETWEEN 6 AND 37.5 AND approximate_longitude BETWEEN 68 AND 97.5
        ) ORDER BY ord, label
        """
    )
    return df["label"].tolist()


@st.cache_data(ttl=3600, show_spinner=False)
def suggest_services(text: str) -> pd.DataFrame:
    exact = next((s for s in SERVICES if s.lower() == (text or "").strip().lower()), None)
    if exact:  # one of the 6 names: answer directly, same as the database would ("Exact name")
        return pd.DataFrame([{"suggestion_rank": 1, "service": exact, "level": "Exact name",
                              "reason": f"Matched the service name ({exact})", "method": "Name match (no LLM)",
                              "is_selected": True, "selection_note": f"Searching for: {exact}"}])
    return run_query(f"SELECT * FROM {SCHEMA}.suggest_services(:t) ORDER BY suggestion_rank", {"t": text})


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_location(text: str) -> pd.DataFrame:
    return run_query(f"SELECT * FROM {SCHEMA}.resolve_location_fast(:t)", {"t": text})  # stored-table copy of resolve_location_v2


@st.cache_data(ttl=600, show_spinner=False)
def find_care(service: str, location_text: str, coords_text: str) -> pd.DataFrame:
    return run_query(
        f"SELECT * FROM {SCHEMA}.find_care_fast(:s, :l, :c) ORDER BY nearest_rank",  # same results as find_care, stored tables
        {"s": service, "l": location_text, "c": coords_text},
    )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def parse_coords(text: str):
    m = COORD_RE.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if 6 <= lat <= 37.5 and 68 <= lon <= 97.5:
        return lat, lon
    return None


def first_url(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    for part in re.split(r"[,\s]+", str(raw)):
        part = part.strip().strip("'\"")
        if not part or part.lower() in ("null", "none"):
            continue
        if not part.lower().startswith(("http://", "https://")):
            part = "https://" + part
        return part
    return None


def clean_text(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    return s if s and s.lower() not in ("null", "none") else None


RAW_FACILITIES = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:[.][A-Za-z0-9-]+)+")
CRITERIA = [
    ("capability", "Not listed under capability"),
    ("procedure", "Not listed under procedure"),
    ("equipment", "Not listed under equipment"),
    ("2 or more", "Mentioned in only one field"),
]
MAX_SCORE = 4  # a source link is not evidence of the service, so it no longer earns a point


def rescore(breakdown) -> tuple[int, str, str]:
    """Score 0-4 from the database's point list, ignoring the source-link point."""
    parts = [p.strip() for p in str(breakdown or "").split(", ") if p.strip() and "source link" not in p.lower()]
    total = sum(int(m.group(1)) for m in (re.match(r"^([+-]\d+)", p) for p in parts) if m)
    total = max(0, min(MAX_SCORE, total))
    label = "Stronger text evidence" if total >= 3 else "Moderate text evidence" if total >= 2 else "Weak text evidence"
    return total, label, ", ".join(parts)
COPY_HTML = """
<style>
  body { margin: 0; }
  #b { width: 100%; min-height: 40px; padding: 4px 8px; border-radius: 8px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
       border: 1px solid rgba(250,250,250,0.2); background: transparent; color: #fafafa;
       font: 400 16px 'Source Sans Pro', sans-serif; }
  #m { font: 12px 'Source Sans Pro', sans-serif; color: #94a3b8; display: block; margin-top: 4px; }
</style>
<button id="b">__LABEL__</button><span id="m"></span>
<script>
  const t = __PAYLOAD__;
  const b = document.getElementById('b');
  try {
    const pb = window.parent.document.querySelector('[data-testid="stBaseButton-secondary"]:not(:disabled)');
    if (pb) {
      const cs = window.parent.getComputedStyle(pb);
      ['color', 'backgroundColor', 'borderColor', 'borderRadius', 'fontFamily', 'fontSize', 'fontWeight'].forEach(p => { b.style[p] = cs[p]; });
    }
  } catch (e) {}
  b.onclick = async () => {
    let ok = false;
    try { await navigator.clipboard.writeText(t); ok = true; } catch (e) {}
    if (!ok) {
      const a = document.createElement('textarea');
      a.value = t; document.body.appendChild(a); a.select();
      try { ok = document.execCommand('copy'); } catch (e) {}
      a.remove();
    }
    document.getElementById('m').textContent = ok ? 'Copied ' + t : 'Copy failed: ' + t;
  };
</script>
"""


def copy_button(text: str | None, label: str, key: str):
    """Copy-to-clipboard button styled like the app's own buttons. Disabled when there is nothing to copy."""
    if not text:
        st.button(f"No {label.split()[-1]}", disabled=True, key=key, use_container_width=True)
        return
    components.html(
        COPY_HTML.replace("__LABEL__", html.escape(label)).replace("__PAYLOAD__", json.dumps(text)),
        height=64,
    )


def esc(s) -> str:
    return html.escape(str(s)).replace(chr(36), "&#36;")


@st.cache_data(ttl=3600, show_spinner=False)
def facility_extras(source_record_id: str) -> dict:
    """Specialties, procedures, equipment (and email if allowed) for one facility record (card only)."""
    df = run_query(
        f"SELECT r.specialties, r.procedure, r.equipment, r.raw_unique_id "
        f"FROM {SCHEMA}.facility_records_tbl r WHERE r.source_record_id = :id LIMIT 1",
        {"id": source_record_id},
    )
    out = df.iloc[0].to_dict() if len(df) else {}
    try:  # email lives in the raw dataset; the app may not have access to it, which must not hide the lists
        em = run_query(
            f"SELECT max(f.email) AS email FROM {RAW_FACILITIES} f WHERE f.unique_id = :u "
            f"AND f.email IS NOT NULL AND trim(f.email) NOT IN ('', 'null', '[]')",
            {"u": str(out.get("raw_unique_id") or "")},
        )
        out["email"] = em.iloc[0]["email"] if len(em) else None
    except Exception:
        out["email"] = None
    return out


def first_email(raw) -> str | None:
    m = EMAIL_RE.search(clean_text(raw) or "")
    return m.group(0) if m else None


def parse_list(raw) -> list[str]:
    s = clean_text(raw)
    if not s:
        return []
    try:
        v = json.loads(s)
    except (ValueError, TypeError):
        v = s
    out, seen = [], set()
    for x in v if isinstance(v, list) else [v]:
        t = str(x).strip()
        if t and t.lower() not in ("null", "none"):
            if re.fullmatch(r"[a-z]+(?:[A-Z][a-z0-9]*)+", t):
                t = re.sub(r"(?<!^)(?=[A-Z])", " ", t).capitalize()
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


def card_css():
    st.markdown(
        "<style>"
        ".cm-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}"
        ".cm-ev{border-radius:8px;padding:8px 12px;font-size:0.9rem}"
        ".cm-ev ul{margin:4px 0 0 0;padding-left:18px}"
        ".cm-ev li{margin:2px 0}"
        ".cm-h{font-weight:600}"
        ".cm-plus{background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.45)}"
        ".cm-plus .cm-h{color:#22c55e}"
        ".cm-minus{background:rgba(239,68,68,0.10);border:1px solid rgba(239,68,68,0.45)}"
        ".cm-minus .cm-h{color:#ef4444}"
        ".cm-muted{opacity:0.65}"
        ".cm-hit{color:#22c55e;font-weight:600}"
        ".cm-why{margin:0 0 6px 0;padding-left:18px;font-size:0.9rem}"
        "</style>",
        unsafe_allow_html=True,
    )


def split_points(breakdown, cautions) -> tuple[list[str], list[str]]:
    """Points earned (+) versus points not earned, deductions and caution flags (-)."""
    parts = [p.strip() for p in str(breakdown or "").split(", ") if p.strip()]
    plus = [p for p in parts if not p.startswith("-")]
    earned = " ".join(plus).lower()
    minus = [label for key, label in CRITERIA if key not in earned]
    minus += [p for p in parts if p.startswith("-")]
    minus += [f"Caution: {c.strip()}" for c in (clean_text(cautions) or "").split(";") if c.strip()]
    return plus, minus


def evidence_columns(plus: list[str], minus: list[str]):
    def block(title, rows, cls):
        lis = "".join(f"<li>{esc(r)}</li>" for r in rows) or "<li class='cm-muted'>None</li>"
        return f"<div class='cm-ev {cls}'><div class='cm-h'>{title}</div><ul>{lis}</ul></div>"

    st.markdown(
        "<div class='cm-grid'>"
        + block("Supports (+)", plus, "cm-plus")
        + block("Gaps and cautions (-)", minus, "cm-minus")
        + "</div>",
        unsafe_allow_html=True,
    )


GENERIC_WORDS = {"surgery", "care", "service", "services"}


@st.fragment
def field_panel(label: str, items: list[str], hit, key: str):
    """One accordion per field: every item (service matches first, in green) plus a search box that filters it."""
    n_hit = sum(1 for i in items if hit(i))
    title = f"{label} ({len(items)})" + (f", {n_hit} mention the service" if n_hit else "")
    with st.expander(title):
        q = st.text_input(
            f"Search {label.lower()}", key=key, placeholder=f"Search {label.lower()}...", label_visibility="collapsed"
        ).strip().lower()
        shown = [i for i in items if q in i.lower()] if q else items
        if not shown:
            st.caption("No items match this search.")
            return
        lis = "".join(f"<li class='cm-hit'>{esc(i)}</li>" if hit(i) else f"<li>{esc(i)}</li>" for i in shown)
        st.markdown(
            "<ul style='max-height:260px;overflow-y:auto;margin:0;padding-left:20px;font-size:0.9rem'>" + lis + "</ul>",
            unsafe_allow_html=True,
        )
        if q:
            st.caption(f"Showing {len(shown)} of {len(items)}.")


def why_fields(extras: dict, service: str, record_id: str = ""):
    """Specialties, procedures and equipment as searchable accordions; items naming the service first."""
    svc = (service or "").lower()
    words = [w for w in re.findall(r"[a-z]+", svc) if len(w) > 2 and w not in GENERIC_WORDS]

    def has(term, text):
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None

    def rank(item):
        t = item.lower()
        if svc and has(svc, t):
            return 0
        return 1 if any(has(w, t) for w in words) else 2

    def hit(item):
        return rank(item) < 2

    key = ", ".join([service] + words) if service else ""
    if key:
        st.caption(f"Green items mention: {key}.")
    for col, label in (("specialties", "Specialties"), ("procedure", "Procedures"), ("equipment", "Equipment")):
        items = sorted(parse_list(extras.get(col)), key=rank)
        if not items:
            st.markdown(f"**{label}:** <span class='cm-muted'>not listed</span>", unsafe_allow_html=True)
            continue
        field_panel(label, items, hit, key=f"why_q_{col}_{record_id}")


from branca.element import MacroElement, Template

APP_DIR = os.path.dirname(os.path.abspath(__file__))
REGION_LEVELS = {
    # level: (file, show from zoom, show up to zoom, click zooms in to at least)
    "state": ("india_states.geojson", 0, 6, 7),
    "district": ("india_districts.geojson", 7, 9, 10),
}


@st.cache_resource(show_spinner=False)
def india_regions(level: str = "state"):
    """State or district outlines for the zoomed-out map layers (community GADM-based files, old boundaries, simplified)."""
    with open(os.path.join(APP_DIR, REGION_LEVELS[level][0])) as f:
        geo = json.load(f)
    index = []
    for k, feat in enumerate(geo["features"]):
        g = feat["geometry"]
        for poly in g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]:
            xs = [p[0] for p in poly[0]]
            ys = [p[1] for p in poly[0]]
            index.append((k, min(xs), min(ys), max(xs), max(ys), poly))
    return geo, index


def _in_ring(x: float, y: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def region_of(lon: float, lat: float, index) -> int | None:
    for k, x0, y0, x1, y1, poly in index:
        if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_ring(lon, lat, poly[0]):
            if not any(_in_ring(lon, lat, hole) for hole in poly[1:]):
                return k
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def service_points(service: str) -> pd.DataFrame:
    return run_query(
        f"SELECT DISTINCT c.facility_id, c.facility_name, c.latitude, c.longitude FROM {SCHEMA}.service_candidates_tbl c "
        f"LEFT JOIN {SCHEMA}.candidate_verification cv ON cv.source_record_id = c.source_record_id "
        f"AND cv.requested_service = c.requested_service "
        f"WHERE c.requested_service = :s AND coalesce(cv.status, 'offered') NOT IN ('not_offered', 'planned') "
        f"AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL",
        {"s": service},
    )


@st.cache_data(ttl=3600, show_spinner=False)
def region_counts(service: str, level: str = "state") -> dict:
    """Facilities matching the service per map region, placed by their coordinates (not the noisy address state)."""
    df = service_points(service)
    _, index = india_regions(level)
    counts = {}
    for lat, lon in zip(df["latitude"], df["longitude"]):
        k = region_of(float(lon), float(lat), index)
        if k is not None:
            counts[k] = counts.get(k, 0) + 1
    return counts


class RegionToggle(MacroElement):
    """Shows a region layer only inside its zoom band, adds a hover label (no tooltip, so clicks do not rerun the
    app), and on click zooms in far enough for the next level to appear."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var layer = {{ this.layer_name }};
  var box = L.control({position: "topright"});
  box.onAdd = function () {
    var d = L.DomUtil.create("div");
    d.style.cssText = "background:rgba(255,255,255,0.92);color:#0f172a;padding:4px 8px;border-radius:6px;font:12px sans-serif;display:none";
    this._div = d;
    return d;
  };
  box.addTo(map);
  layer.eachLayer(function (l) {
    l.on("mouseover", function () {
      var p = l.feature.properties;
      box._div.innerHTML = "<b>" + p.name + "</b><br>" + p.n + " facilities list this service. Click to zoom in.";
      box._div.style.display = "block";
    });
    l.on("mouseout", function () { box._div.style.display = "none"; });
    l.on("click", function () {
      var b = l.getBounds();
      map.setView(b.getCenter(), Math.max(map.getBoundsZoom(b), {{ this.click_zoom }}));
    });
  });
  function toggle() {
    var z = map.getZoom();
    if (z >= {{ this.min_zoom }} && z <= {{ this.max_zoom }}) {
      if (!map.hasLayer(layer)) { layer.addTo(map); }
      layer.bringToBack();
    } else if (map.hasLayer(layer)) {
      map.removeLayer(layer);
      box._div.style.display = "none";
    }
  }
  map.on("zoomend", toggle);
  toggle();
})();
{% endmacro %}
""")

    def __init__(self, layer, min_zoom: int, max_zoom: int, click_zoom: int):
        super().__init__()
        self._name = "RegionToggle"
        self.layer_name = layer.get_name()
        self.min_zoom = min_zoom
        self.max_zoom = max_zoom
        self.click_zoom = click_zoom


def add_region_layer(m, service: str):
    """Two shaded levels: states when far out, districts one step closer. Hover highlights, click zooms in."""
    for level, (_, min_zoom, max_zoom, click_zoom) in REGION_LEVELS.items():
        try:
            geo, _ = india_regions(level)
            counts = region_counts(service, level)
        except Exception:
            continue
        geo = json.loads(json.dumps(geo))
        top = max(counts.values(), default=0) or 1
        for k, feat in enumerate(geo["features"]):
            feat["properties"]["n"] = counts.get(k, 0)
        layer = folium.GeoJson(
            geo,
            name=f"Regions ({level})",
            style_function=lambda f, top=top: {
                "fillColor": "#3b82f6",
                "color": "#64748b",
                "weight": 0.7,
                "fillOpacity": 0.04 + 0.56 * f["properties"]["n"] / top,
            },
            highlight_function=lambda f: {"color": "#f59e0b", "weight": 2.5, "fillOpacity": 0.7},
        )
        layer.add_to(m)
        RegionToggle(layer, min_zoom, max_zoom, click_zoom).add_to(m)


def strength_color(score: int) -> str:
    if score >= 4:
        return "green"
    if score >= 2:
        return "orange"
    return "gray"



RANKING_EXPLAINER = """
**In short:** TilikCare lists the 20 nearest facilities whose records mention the service, and shows how strongly
the text supports it. It does not confirm that the service is available today. Always call ahead.
In a life-threatening emergency, call 108 (ambulance in most Indian states) first.

#### 1. Where the data comes from
- Facility records come from the Virtue Foundation healthcare facility dataset for India, provided for this
  datathon. Each record keeps a link to the page it came from.
- Records are cleaned: duplicate rows for one facility are merged, phone numbers and websites are tidied, and
  facilities without usable map coordinates inside India are left out.
- Records can be out of date: services, phone numbers and opening hours may have changed.

#### 2. How your request becomes a service
- Picking a service name from the list is a direct match. No AI is used.
- "Describe the case" sends your words to an AI model (Llama 3.3 70B) that may only answer with the 6 supported
  services. Its High/Medium/Low level is the model's own estimate, not a probability. It is not a diagnosis.
- Only if the AI finds no service, a fallback tries known synonyms, then similarity of meaning (embeddings), then
  the AI once more. The "How we read your request" box always says which route was used.

#### 3. Which facilities can appear
1. **Text match:** the facility's record mentions the service or a known synonym, in fields such as capability,
   procedure or equipment.
2. **AI check of the cited text:** an AI model (Llama 3.3 70B) reads the matched text and judges whether it says
   the facility offers the service. Facilities judged "not offered" or "planned" are removed. "Unclear" ones stay,
   with a caution on the card. The quote shown is checked to be copied word for word from the record. A facility
   that has not been checked appears as a plain text match.
3. **Usable location:** the facility has map coordinates inside India.

#### 4. How they are ordered
- By straight-line distance from the patient's location, nearest first. Only the 20 nearest are shown.
- The patient's location is your pin or pasted coordinates (exact), a PIN code centre, or for a city an
  approximate centre (the median of PIN centres in that district). The note under step 2 says which.
- Straight-line distance is not road distance or travel time: water, hills and traffic can make a near facility
  slow to reach. For the selected facility the details also show the road distance and an estimated drive time
  (OpenStreetMap roads, no traffic), and a button opens the route in Google Maps.
- "Stronger evidence first" re-orders the same 20: facilities scoring 3/4 or more come first, each group by distance.

#### 5. What the evidence score means (0 to 4)
It rates how well the record's text supports the service. It is not a rating of hospital quality.

| Signal in the record | Points |
| --- | --- |
| Listed under capability | +1 |
| Listed under procedure | +1 |
| Listed under equipment | +1 |
| Mentioned in 2 or more fields | +1 |
| Wording suggests the service is not offered or only planned | -2 |
| Long text may describe another facility | -1 |
| Duplicate or identity warning | -1 |
| Location needs review | -1 |

The total is kept between 0 and 4: 3-4 is stronger, 2 moderate and 0-1 weak text evidence. A link to the source page is shown but earns no point: it proves where the text came from, not that the service exists. On the card, the
green box lists the points earned; the red box lists points not earned, deductions and cautions.

#### 6. What the rest of the card and map show
- **Why this facility:** everything the record lists under specialties, procedures and equipment. Green items
  mention the service's words. This is word matching, so a related item worded differently stays plain: use the
  search box inside each list.
- **Source text:** the passage that matched, with a link to the page it came from.
- **Reference icon** next to the service: an official explainer of the service (WHO or MedlinePlus, US National
  Library of Medicine). It says nothing about this facility.
- **Map shading:** zoomed out, states and then districts are shaded by how many facility records mention the
  service. More records does not mean more available care. Boundaries are simplified community data, not
  official maps.

#### 7. What TilikCare does not know
Current availability, free beds, waiting time, cost, insurance or government-scheme acceptance, quality of care,
and live traffic. Confirm these by phone before moving a patient.
"""

# --------------------------------------------------------------------------------------
# Routing (selected facility only). OpenRouteService if ORS_API_KEY is set, else the
# public OSRM demo server. Both use OpenStreetMap roads; no traffic. Fails quietly.
# --------------------------------------------------------------------------------------
import time
import urllib.request

ORS_API_KEY = os.getenv("ORS_API_KEY", "")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def road_route(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> dict | None:
    """Returns {"coords": [[lat, lon], ...], "km": float, "min": float, "provider": str} or None."""
    try:
        if ORS_API_KEY:
            req = urllib.request.Request(
                "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                data=json.dumps({"coordinates": [[o_lon, o_lat], [d_lon, d_lat]]}).encode(),
                headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                feat = json.loads(r.read())["features"][0]
            summ = feat["properties"]["summary"]
            coords, km, mins, provider = feat["geometry"]["coordinates"], summ["distance"] / 1000, summ["duration"] / 60, "OpenRouteService"
        else:
            url = (f"https://router.project-osrm.org/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}"
                   "?overview=full&geometries=geojson")
            req = urllib.request.Request(url, headers={"User-Agent": "TilikCare-datathon"})
            with urllib.request.urlopen(req, timeout=8) as r:
                route = json.loads(r.read())["routes"][0]
            coords, km, mins, provider = route["geometry"]["coordinates"], route["distance"] / 1000, route["duration"] / 60, "OSRM demo"
        return {"coords": [[c[1], c[0]] for c in coords], "km": km, "min": mins, "provider": provider}
    except Exception:
        return None


# Cards + map in one custom component, so hovering a card highlights its map pin (and back) in the browser.
tilik_map = components.declare_component("tilik_map", path=os.path.join(APP_DIR, "tilik_map"))

# --------------------------------------------------------------------------------------
# Page frame. The search bar, filters, cards and map all live in the tilik_map component.
# --------------------------------------------------------------------------------------
st.markdown(
    "<style>"
    "header[data-testid='stHeader']{background:transparent;height:0}"
    "div.block-container{padding:0.4rem 1.2rem 0 1.2rem;max-width:100%}"
    "[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl']{display:none}"
    "div[data-testid='stPopover'] button{border-radius:999px;padding:2px 12px;min-height:0}"
    "[data-stale='true']{opacity:1 !important}"  # the map shows its own "Loading" pill instead of Streamlit's fade
    "</style>",
    unsafe_allow_html=True,
)
# Landing page first; "Open Dashboard" links to ?view=dashboard
if st.query_params.get("view") != "dashboard":
    from landing import render_landing
    render_landing()
    st.stop()

COORD_KEY = "coords:"
ss = st.session_state
filters = ss.setdefault("filters", {"min_ev": 0, "max_km": 300, "order": "near", "hide_unclear": False})
min_ev, max_km, hide_unclear = filters["min_ev"], filters["max_km"], filters["hide_unclear"]
order = "Stronger evidence first" if filters["order"] == "ev" else "Nearest first"

# Thin notice strip above the app
n1, n2 = st.columns([6, 1.3], vertical_alignment="center")
with n1:
    st.caption((":red[**MOCK MODE:** invented sample facilities, not real data.] " if MOCK else "") +
               "Text matches from facility records, not verified availability. Always call ahead. "
               "Does not diagnose. Emergency: call **108**.")
with n2:
    with st.popover("How the ranking works", use_container_width=True):  # no icon=: older Streamlit on Databricks Apps
        st.markdown(RANKING_EXPLAINER)

# Events from the component, handled at the top of the run so this same run shows the result.
# (An explicit st.rerun() made Streamlit drop the map while the next run was still querying.)
event = ss.get("tilik_map")  # the component's last value (kept by Streamlit under its key)
if event and event.get("t") != ss.get("last_event_t"):
    ss["last_event_t"] = event.get("t")
    kind = event.get("kind")
    if kind == "pin" and parse_coords(f"{event['lat']}, {event['lon']}"):
        ss["place_text"], ss["place_label"] = f"{COORD_KEY}{event['lat']},{event['lon']}", f"Dropped pin ({event['lat']}, {event['lon']})"
        if event.get("service"):  # keep what was typed or picked in "What care"
            ss["service_text"] = event["service"]
        ss["selected_record"] = None
    elif kind == "select":
        ss["selected_record"] = event.get("id")
    elif kind == "search":
        ss["service_text"] = event.get("service") or None
        ss["place_text"] = event.get("place") or None
        ss["place_label"] = event.get("place_text") if (event.get("place") or "").startswith(COORD_KEY) else None
        ss["selected_record"] = None
        if ss["service_text"] and ss["place_text"]:
            label = ss["place_label"] or ss["place_text"].split("|", 1)[-1].strip()
            rec = [ss["service_text"], ss["place_text"], label]
            ss["recent"] = [rec] + [x for x in ss.get("recent", []) if x[:2] != rec[:2]][:4]
    elif kind == "use_service" and ss.get("service_text"):
        ss.setdefault("service_override", {})[ss["service_text"]] = event.get("service")
    elif kind == "reset":  # Reset map button: back to the first view
        for k in ("service_text", "place_text", "place_label", "selected_record", "service_override"):
            ss.pop(k, None)
        ss["filters"] = {"min_ev": 0, "max_km": 300, "order": "near", "hide_unclear": False}
    elif kind == "home":
        st.query_params.clear()
        st.rerun()  # show the landing page
    elif kind == "filters":
        ss["filters"] = {"min_ev": int(event.get("min_ev", 0)), "max_km": int(event.get("max_km", 300)),
                         "order": event.get("order", "near"), "hide_unclear": bool(event.get("hide_unclear"))}

filters = ss["filters"]  # may have just changed (filters / reset)
min_ev, max_km, hide_unclear = filters["min_ev"], filters["max_km"], filters["hide_unclear"]
order = "Stronger evidence first" if filters["order"] == "ev" else "Nearest first"

# ---------------------------------- Interpret the need ------------------------------
chosen_service, read, notes = None, None, []
service_text = ss.get("service_text")
if service_text:
    sugg = suggest_services(service_text)
    valid = sugg[sugg["service"].notna()]
    if valid.empty:
        notes.append(sugg["selection_note"].iloc[0] if len(sugg) else "No supported service matched.")
    else:
        override = ss.get("service_override", {}).get(service_text)
        auto = valid[valid["is_selected"]]
        chosen_service = override or (auto["service"].iloc[0] if not auto.empty else None)
        chosen_row = valid[valid["service"] == chosen_service]
        method = chosen_row["method"].iloc[0] if not chosen_row.empty else ""
        read = {"chosen": chosen_service, "ref": SERVICE_REFS.get(chosen_service),
                "method": ("chosen by you" if override else
                           "matched by name" if "name" in str(method).lower() else
                           "matched by meaning" if "meaning" in str(method).lower() or "embedding" in str(method).lower() else
                           "interpreted by AI (Llama 3.3), not a diagnosis"),
                "options": [{"service": r["service"], "level": str(r["level"]), "reason": clean_text(r["reason"])}
                            for _, r in valid.iterrows()]}

# ---------------------------------- Resolve the location ----------------------------
location_text, coords_text, origin, place_note = "none", "none", None, ""
place = ss.get("place_text")
if place and place.startswith(COORD_KEY):
    lat, lon = (float(x) for x in place[len(COORD_KEY):].split(","))
    if parse_coords(f"{lat}, {lon}"):
        origin, coords_text = (lat, lon), f"{lat}, {lon}"
        place_note = ss.get("place_label") or f"{lat}, {lon}"
    else:
        notes.append("These coordinates are outside India.")
elif place:
    location_text = place
    loc = resolve_location(place)
    if not loc.empty and pd.notna(loc.iloc[0]["origin_latitude"]):
        r = loc.iloc[0]
        origin = (float(r["origin_latitude"]), float(r["origin_longitude"]))
        place_note = place.split("|", 1)[-1].strip()
    elif not loc.empty:
        notes.append(loc.iloc[0]["location_note"])

# ---------------------------------- Search + filters --------------------------------
results = pd.DataFrame()
if chosen_service and origin is not None:
    results = find_care(chosen_service, location_text, coords_text)
    if not results.empty:
        rs = results["strength_breakdown"].map(rescore)
        results = results.assign(evidence_strength=rs.str[0], strength_label=rs.str[1], strength_breakdown=rs.str[2])
        results = results[(results["evidence_strength"] >= min_ev) & (results["distance_km"] <= max_km)]
        if hide_unclear:
            results = results[~results["caution_flags"].fillna("").str.contains("AI check", case=False)]
        if order == "Stronger evidence first":
            results = results.assign(_g=(results["evidence_strength"] < 3).astype(int)).sort_values(["_g", "distance_km"])
        else:
            results = results.sort_values("distance_km")
        results = results.reset_index(drop=True)
        results["display_rank"] = range(1, len(results) + 1)

ids = set(results["source_record_id"]) if not results.empty else set()
selected_id = st.session_state.get("selected_record")
if selected_id not in ids:
    selected_id = None

cards = []
for _, r in results.iterrows():
    plus, minus = split_points(r["strength_breakdown"], r["caution_flags"])
    cards.append({
        "id": str(r["source_record_id"]), "rank": int(r["display_rank"]), "name": str(r["facility_name"]),
        "address": clean_text(r["full_address"]), "lat": float(r["latitude"]), "lon": float(r["longitude"]),
        "km": float(r["distance_km"]), "ev": int(r["evidence_strength"]), "label": clean_text(r["strength_label"]) or "",
        "quote": (clean_text(r["evidence_quote"]) or "")[:300] or None, "source": first_url(r["source_url"]),
        "phone": clean_text(r["phone"]), "website": first_url(r["website"]), "caution": clean_text(r["caution_flags"]),
        "why": f"#{int(r['display_rank'])} nearest: {float(r['distance_km']):.1f} km straight-line. Evidence {int(r['evidence_strength'])}/{MAX_SCORE} ({r['strength_label']}).", "plus": plus, "minus": minus, "fields": clean_text(r["evidence_fields"]),
    })

detail, route = None, None
if selected_id:
    row = results[results["source_record_id"] == selected_id].iloc[0]
    route = road_route(origin[0], origin[1], float(row["latitude"]), float(row["longitude"]))
    extras = {}
    try:
        extras = facility_extras(str(selected_id))
    except Exception:
        pass
    detail = {"id": str(selected_id), "route": route, "email": first_email(extras.get("email")),
              "specialties": parse_list(extras.get("specialties")), "procedures": parse_list(extras.get("procedure")),
              "equipment": parse_list(extras.get("equipment"))}

# Region shading and facility dots for the chosen service (before a search, and as context)
state_counts = district_counts = None
points = []
if chosen_service:
    try:
        state_counts = {str(k): v for k, v in region_counts(chosen_service, "state").items()}
        district_counts = {str(k): v for k, v in region_counts(chosen_service, "district").items()}
        sp = service_points(chosen_service)
        points = [[float(a), float(b), str(n)] for a, b, n in zip(sp["latitude"], sp["longitude"], sp["facility_name"])][:3000]
    except Exception:
        pass

if not chosen_service and origin is not None:
    title, empty_html = f"Patient location set: {place_note}", (
        "<b>Now choose the care needed</b> in <b>What care</b> at the top (for example Dialysis, MRI, NICU), "
        "or describe the case in your own words. The nearby options will appear here.<br><br>"
        "Nothing is listed yet because the options depend on the service: a facility that does MRI may not do dialysis.")
elif not chosen_service:
    title, empty_html = "Find care near the patient", (
        "<b>1.</b> Type the care needed above (a service, or describe the case in your own words).<br>"
        "<b>2.</b> Type a place, PIN or coordinates in <b>Where</b>, choose <b>Nearby</b>, or <b>right-click the map</b> to drop the patient pin.<br><br>"
        "Explore the map: click a state to see its districts.")
elif origin is None:
    title, empty_html = f"{chosen_service}: where is the patient?", (
        "Type a place, PIN or coordinates in <b>Where</b>, choose <b>Nearby</b> to use your location, or "
        "<b>right-click the map</b> to drop the patient pin.<br><br>"
        "Shading shows how many facility records mention this service. Click a state, then a district, to see the "
        "facilities as dots.")
elif results.empty:
    title, empty_html = f"No {chosen_service.lower()} options found", (
        "No sufficiently supported candidates match this search and these filters. This does not mean no care exists "
        "in the area; the records may not mention this service. Try loosening the filters.")
else:
    title, empty_html = f"{len(results)} {chosen_service.lower()} options near {place_note}", ""

# Places for the "Where" dropdown: city names (PINs and coordinates are recognised when typed in full)
try:
    city_labels = [o for o in location_options() if o.startswith("City |")]
except Exception:
    city_labels = []
ui = {"need_text": service_text or "", "need_value": service_text,
      "where_text": ss.get("place_label") or ((place or "").split("|", 1)[-1].strip() if place else ""), "where_value": place}

# Mock mode: open on the sample-data area and mark where the invented hospitals and cities are
demo = None
if MOCK:
    import mock_backend
    demo = {"bounds": [[24.55, 84.75], [25.75, 85.45]],
            "cities": [[24.7914, 85.0002, "Gaya"], [25.5941, 85.1376, "Patna"]],
            "facilities": [[f[2], f[3], f[1]] for f in mock_backend.FACILITIES]}

ss["run_no"] = ss.get("run_no", 0) + 1  # changes every run, so the component always gets a fresh render (clears "Loading")
tilik_map(
    run_no=ss["run_no"], demo=demo, ui=ui, read=read, notes=notes, filters=filters, places=city_labels,
    suggest_places=city_labels[:6] if MOCK else [], recent=ss.get("recent", []),
    terms=SERVICE_TERMS.get(chosen_service), service=chosen_service, origin=list(origin) if origin else None, results=cards, selected=selected_id,
    detail=detail, route=route, points=points, state_counts=state_counts, district_counts=district_counts,
    title=title, subtitle="Hover a card to find it on the map. Click a card for the full evidence checklist.",
    empty_html=empty_html, open_detail=bool(selected_id),
    fit_key=f"{chosen_service}|{origin}|{len(cards)}|{json.dumps(filters, sort_keys=True)}",
    key="tilik_map", default=None,
)

