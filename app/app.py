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

st.set_page_config(page_title="TilikCare: Find Care with Evidence", layout="wide")

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
COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*$")

# --------------------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------------------
cfg = Config()  # picks up the app's service principal credentials automatically


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


@st.cache_data(ttl=3600, show_spinner=False)
def location_options() -> list[str]:
    df = run_query(
        f"""
        SELECT label FROM (
          SELECT concat('City | ', city, ' (', state, ')') AS label, 1 AS ord FROM {SCHEMA}.city_origins
          UNION ALL
          SELECT concat('PIN ', postcode, ' | ', initcap(array_join(districts, '/')), ' (', initcap(array_join(states, '/')), ')'), 2
          FROM {SCHEMA}.pincode_reference
          WHERE approximate_latitude BETWEEN 6 AND 37.5 AND approximate_longitude BETWEEN 68 AND 97.5
        ) ORDER BY ord, label
        """
    )
    return df["label"].tolist()


@st.cache_data(ttl=3600, show_spinner=False)
def suggest_services(text: str) -> pd.DataFrame:
    return run_query(f"SELECT * FROM {SCHEMA}.suggest_services(:t) ORDER BY suggestion_rank", {"t": text})


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_location(text: str) -> pd.DataFrame:
    return run_query(f"SELECT * FROM {SCHEMA}.resolve_location_v2(:t)", {"t": text})


@st.cache_data(ttl=600, show_spinner=False)
def find_care(service: str, location_text: str, coords_text: str) -> pd.DataFrame:
    return run_query(
        f"SELECT * FROM {SCHEMA}.find_care(:s, :l, :c) ORDER BY nearest_rank",
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
    ("source link", "No source link"),
]
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
    """Specialties, procedures, equipment and email for one facility record (card only)."""
    df = run_query(
        f"SELECT r.specialties, r.procedure, r.equipment, "
        f"(SELECT max(f.email) FROM {RAW_FACILITIES} f WHERE f.unique_id = r.raw_unique_id "
        f"AND f.email IS NOT NULL AND trim(f.email) NOT IN ('', 'null', '[]')) AS email "
        f"FROM {SCHEMA}.facility_records r WHERE r.source_record_id = :id LIMIT 1",
        {"id": source_record_id},
    )
    return df.iloc[0].to_dict() if len(df) else {}


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
        f"SELECT DISTINCT c.facility_id, c.latitude, c.longitude FROM {SCHEMA}.service_candidates c "
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


# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------
st.markdown(
    "<style>"
    ".tc-hero{background:linear-gradient(135deg,#0f766e 0%,#1e3a8a 100%);border-radius:14px;"
    "padding:18px 24px;margin:0 0 10px 0;box-shadow:0 2px 10px rgba(0,0,0,0.15)}"
    ".tc-name{font-size:2.3rem;font-weight:700;color:#ffffff;letter-spacing:0.5px;line-height:1.2}"
    ".tc-tag{font-size:1.05rem;color:#ccfbf1;margin-top:4px}"
    "</style>"
    "<div class='tc-hero'>"
    "<div class='tc-name'>TilikCare</div>"
    "<div class='tc-tag'>Find care, with the evidence behind it</div>"
    "</div>",
    unsafe_allow_html=True,
)
st.caption(
    "Results are text matches from facility records, not verified service availability. "
    "Distance is straight-line, not road distance. Always call ahead. This tool does not diagnose."
)

# --------------------------------------------------------------------------------------
# 1. What care is needed
# --------------------------------------------------------------------------------------
st.subheader("1. What care is needed?")


def search_services(term: str):
    term = (term or "").strip()
    if not term:
        return [(s, s) for s in SERVICES]
    t = term.lower()
    matches = [(s, s) for s in SERVICES if t in s.lower()]
    if term.lower() not in [s.lower() for s in SERVICES]:
        matches.append((f'Describe the case: "{term}"', term))
    return matches


@st.fragment
def service_picker():
    # Typing only reruns this fragment; the full page reruns once a value is chosen.
    value = st_searchbox(
        search_services,
        key="service_box",
        placeholder="Type a service (e.g. dial...) or describe the case in your own words",
        default_options=[(s, s) for s in SERVICES],
        clear_on_submit=False,
        rerun_scope="fragment",
        debounce=300,
    )
    if value != st.session_state.get("service_text"):
        st.session_state["service_text"] = value
        st.rerun(scope="app")


service_picker()
service_text = st.session_state.get("service_text")

chosen_service = None
if service_text:
    with st.spinner("Reading your request..."):
        sugg = suggest_services(service_text)
    valid = sugg[sugg["service"].notna()]
    if valid.empty:
        st.warning(sugg["selection_note"].iloc[0] if len(sugg) else "No supported service matched.")
    else:
        override = st.session_state.get("service_override", {}).get(service_text)
        auto = valid[valid["is_selected"]]
        chosen_service = override or (auto["service"].iloc[0] if not auto.empty else None)

        with st.container(border=True):
            st.markdown("**How we read your request**")
            for _, r in valid.iterrows():
                c1, c2, c3 = st.columns([3, 6, 2])
                ref = SERVICE_REFS.get(r["service"])
                link = f" [:material/open_in_new:]({ref[1]})" if ref else ""
                about = f" | Reference: {ref[0]}" if ref else ""
                c1.markdown(f"**{r['service']}**{link}  \nLevel: {r['level']}{about}")
                c2.caption(f"{r['reason']}  \n{r['method']}")
                is_current = r["service"] == chosen_service
                if c3.button(
                    "Selected" if is_current else "Use this",
                    key=f"use_{r['service']}",
                    disabled=is_current,
                    use_container_width=True,
                ):
                    st.session_state.setdefault("service_override", {})[service_text] = r["service"]
                    st.rerun()
            if chosen_service:
                st.success(f"Selected: {chosen_service}")
            else:
                st.info("Not confident enough to choose. Pick one of the suggestions above.")

# --------------------------------------------------------------------------------------
# 2. Where is the patient
# --------------------------------------------------------------------------------------
st.subheader("2. Where is the patient?")
mode = st.radio(
    "Set the location by",
    ["Search a place", "Paste coordinates", "Drop a pin on the map"],
    horizontal=True,
    label_visibility="collapsed",
)

location_text, coords_text, origin = "none", "none", None

if mode == "Search a place":
    def search_places(term: str):
        term = (term or "").strip().lower()
        if len(term) < 2:
            return []
        opts = location_options()
        starts = [o for o in opts if term in o.lower().split("|", 1)[-1].strip().lower()[: len(term)]]
        contains = [o for o in opts if term in o.lower() and o not in starts]
        return (starts + contains)[:25]

    @st.fragment
    def place_picker():
        value = st_searchbox(
            search_places,
            key="place_box",
            placeholder="Type a city or PIN code, e.g. ahmed or 380001",
            clear_on_submit=False,
            rerun_scope="fragment",
            debounce=300,
        )
        if value != st.session_state.get("place_text"):
            st.session_state["place_text"] = value
            st.rerun(scope="app")

    place_picker()
    place = st.session_state.get("place_text")
    if place:
        location_text = place
        loc = resolve_location(place)
        if not loc.empty:
            r = loc.iloc[0]
            if pd.notna(r["origin_latitude"]):
                origin = (float(r["origin_latitude"]), float(r["origin_longitude"]))
                st.caption(f"{r['location_note']} ({r['origin_precision']})")
            else:
                st.warning(r["location_note"])

elif mode == "Paste coordinates":
    raw = st.text_input("Coordinates (lat, long)", placeholder="23.0225, 72.5714")
    if raw:
        parsed = parse_coords(raw)
        if parsed:
            origin = parsed
            coords_text = f"{parsed[0]}, {parsed[1]}"
            st.caption("Distances are measured from these exact coordinates.")
        else:
            st.warning("Could not read these coordinates, or they are outside India. Use the format: 23.0225, 72.5714")

else:
    pin = st.session_state.get("pin")
    pin_map = folium.Map(location=pin or INDIA_CENTER, zoom_start=11 if pin else 5, control_scale=True)
    if pin:
        folium.Marker(pin, tooltip="Patient location", icon=folium.Icon(color="blue", icon="user")).add_to(pin_map)
    st.caption("Click on the map to place the patient's location.")
    out = st_folium(pin_map, height=380, use_container_width=True, key="pin_map", returned_objects=["last_clicked"], center=pin or INDIA_CENTER, zoom=11 if pin else 5)
    if out and out.get("last_clicked"):
        new_pin = (round(out["last_clicked"]["lat"], 5), round(out["last_clicked"]["lng"], 5))
        if new_pin != pin:
            st.session_state["pin"] = new_pin
            st.rerun()
    if pin:
        if parse_coords(f"{pin[0]}, {pin[1]}"):
            origin = pin
            coords_text = f"{pin[0]}, {pin[1]}"
            st.caption(f"Pin: {pin[0]}, {pin[1]}. Distances are measured from this point.")
        else:
            st.warning("The pin is outside India. Place it inside India.")

# --------------------------------------------------------------------------------------
# 3. Results
# --------------------------------------------------------------------------------------
if not chosen_service or origin is None:
    st.info("Next: choose where the patient is (step 2) to see nearby options." if chosen_service else "Choose the care needed and the patient's location to see nearby options.")
    st.stop()

with st.spinner("Finding nearby options... The first search can take up to 30 seconds."):
    results = find_care(chosen_service, location_text, coords_text)

if results.empty:
    st.warning(
        "No sufficiently supported candidates found for this search. "
        "This does not mean no care exists in the area; the records may not mention this service."
    )
    st.stop()

st.subheader("3. Nearby options")
order = st.radio(
    "Order results by",
    ["Nearest first", "Stronger evidence first"],
    horizontal=True,
    help="Stronger evidence first: facilities scoring 3/5 or more come first, each group ordered by distance. "
    "Only the 20 nearest candidates are considered either way.",
)
if order == "Stronger evidence first":
    results = results.assign(_g=(results["evidence_strength"] < 3).astype(int)).sort_values(["_g", "distance_km"])
else:
    results = results.sort_values("distance_km")
results = results.reset_index(drop=True)
results["display_rank"] = range(1, len(results) + 1)

selected_id = st.session_state.get("selected_record")
if selected_id not in set(results["source_record_id"]):
    selected_id = None

map_col, card_col = st.columns([3, 2], gap="medium")

with map_col:
    m = folium.Map(location=origin, zoom_start=11, control_scale=True)
    add_region_layer(m, chosen_service)
    folium.Marker(origin, tooltip="Patient location", icon=folium.Icon(color="blue", icon="user")).add_to(m)
    for _, r in results.iterrows():
        popup = (
            f"<b>#{r['display_rank']} {html.escape(str(r['facility_name']))}</b><br>"
            f"{html.escape(str(r['full_address'] or ''))}<br>"
            f"{html.escape(str(r['distance_label']))}<br>"
            f"Evidence {r['evidence_strength']}/5 ({html.escape(str(r['strength_label']))})<br>"
            f"Mentioned in: {html.escape(str(r['evidence_fields'] or 'n/a'))}"
        )
        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=9 if r["source_record_id"] == selected_id else 7,
            color=strength_color(int(r["evidence_strength"])),
            fill=True,
            fill_opacity=0.85,
            tooltip=f"#{r['display_rank']} {r['facility_name']}",
            popup=folium.Popup(popup, max_width=320),
        ).add_to(m)
    bounds = [origin] + list(zip(results["latitude"], results["longitude"]))
    m.fit_bounds(bounds, padding=(20, 20))
    st.caption("Marker colour: green 4-5/5, orange 2-3/5, grey 0-1/5 evidence. Click a marker, a table row, or use the picker to show details.")
    map_out = st_folium(
        m, height=470, use_container_width=True, key="result_map", returned_objects=["last_object_clicked_tooltip"]
    )
    tip = (map_out or {}).get("last_object_clicked_tooltip")
    if tip and tip != st.session_state.get("last_marker_tip"):
        st.session_state["last_marker_tip"] = tip
        hit = re.match(r"\s*#(\d+) ", str(tip))
        if hit:
            match = results[results["display_rank"] == int(hit.group(1))]
            if not match.empty:
                st.session_state["selected_record"] = match["source_record_id"].iloc[0]
                st.rerun()

with card_col:
    labels = ["Highest suggested facility"] + [
        f"#{r['display_rank']} {r['facility_name']}" for _, r in results.iterrows()
    ]
    ids = [None] + results["source_record_id"].tolist()
    current = ids.index(selected_id) if selected_id in ids else 0
    pick = st.selectbox("Show in the card", labels, index=current)
    if ids[labels.index(pick)] != selected_id:
        st.session_state["selected_record"] = ids[labels.index(pick)]
        st.rerun()
    if selected_id:
        row = results[results["source_record_id"] == selected_id].iloc[0]
        heading = "Selected facility"
    else:
        row = results.iloc[0]
        heading = "Highest Suggested Facility"
    with st.container(border=True):
        st.markdown(f"#### {heading}")
        st.markdown(f"### {row['facility_name']}")
        st.markdown(f"{row['full_address'] or 'Address not listed'}")
        c1, c2 = st.columns(2)
        c1.metric("Distance", f"{row['distance_km']:.1f} km", help="Straight-line, not road distance")
        c2.metric("Evidence", f"{int(row['evidence_strength'])}/5", help=row["strength_label"])
        extras = {}
        try:
            extras = facility_extras(str(row["source_record_id"]))
        except Exception:
            st.caption("Extra facility details could not be loaded right now.")
        card_css()
        st.markdown("**Evidence breakdown**")
        plus, minus = split_points(row["strength_breakdown"], row["caution_flags"])
        evidence_columns(plus, minus)
        url = first_url(row["website"])
        email = first_email(extras.get("email"))
        phone = clean_text(row["phone"])
        b1, b2, b3 = st.columns(3)
        with b1:
            st.link_button("Website", url or "https://", disabled=url is None, use_container_width=True)
        with b2:
            st.link_button("Email", f"mailto:{email}" if email else "https://", disabled=email is None, use_container_width=True)
        with b3:
            copy_button(phone, "Copy phone", key=f"copy_{row['source_record_id']}")
        contact = [x for x in (phone and f"Phone: {phone}", email and f"Email: {email}") if x]
        if contact:
            st.caption(" | ".join(contact))
        if selected_id and st.button("Back to highest suggested", use_container_width=True):
            st.session_state["selected_record"] = None
            st.session_state["last_marker_tip"] = None
            st.rerun()

with map_col:
    with st.container(border=True):
        st.markdown(f"**Why {row['facility_name']}**")
        st.caption(f"#{int(row['nearest_rank'])} nearest, {row['distance_label']}. Text match, not verified. Call ahead to confirm.")
        why_fields(extras, chosen_service, str(row["source_record_id"]))
        quote = clean_text(row["evidence_quote"])
        if quote:
            src = first_url(row["source_url"])
            more = "..." if len(quote) > 300 else ""
            link = f" [source]({src})" if src else ""
            st.caption(f'Source text: "{quote[:300]}{more}"{link}')

st.markdown("**All nearby options** (select a row to show it in the card)")
table = results[["display_rank", "facility_name", "distance_km", "evidence_strength", "strength_label", "why_this"]]
event = st.dataframe(
    table,
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "display_rank": st.column_config.NumberColumn("#", width="small"),
        "facility_name": st.column_config.TextColumn("Facility", width="medium"),
        "distance_km": st.column_config.NumberColumn("Distance (km, straight-line)", format="%.1f"),
        "evidence_strength": st.column_config.ProgressColumn("Evidence", min_value=0, max_value=5, format="%d/5"),
        "strength_label": st.column_config.TextColumn("Evidence level"),
        "why_this": st.column_config.TextColumn("Why", width="large"),
    },
    key="results_table",
)
rows = list(event.selection.rows) if event and hasattr(event, "selection") else []
if rows != st.session_state.get("last_table_rows", []):
    st.session_state["last_table_rows"] = rows
    if rows:
        st.session_state["selected_record"] = results.iloc[rows[0]]["source_record_id"]
        st.rerun()

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
  slow to reach.
- "Stronger evidence first" re-orders the same 20: facilities scoring 3/5 or more come first, each group by distance.

#### 5. What the evidence score means (0 to 5)
It rates how well the record's text supports the service. It is not a rating of hospital quality.

| Signal in the record | Points |
| --- | --- |
| Listed under capability | +1 |
| Listed under procedure | +1 |
| Listed under equipment | +1 |
| Mentioned in 2 or more fields | +1 |
| Has a source link | +1 |
| Wording suggests the service is not offered or only planned | -2 |
| Long text may describe another facility | -1 |
| Duplicate or identity warning | -1 |
| Location needs review | -1 |

The total is kept between 0 and 5: 4-5 is stronger, 2-3 moderate and 0-1 weak text evidence. On the card, the
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
and travel time. Confirm these by phone before moving a patient.
"""

with st.expander("How the ranking works, and what to trust"):
    st.markdown(RANKING_EXPLAINER)
