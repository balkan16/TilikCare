"""Offline stand-in for the Databricks SQL functions, for UI work only.

Turn on with CAREMATCH_MOCK=1. app.py then calls run_query() from here instead of Databricks.
The facilities are INVENTED sample data (names, phones, coordinates), shaped like the real
find_care / suggest_services / resolve_location_v2 outputs. Never use them as real results.
"""
import json
import math
import re

import pandas as pd

SERVICES = ["Dialysis", "Emergency C-section", "NICU", "Cardiac surgery", "MRI", "Burn care"]

# Place labels shown by the place search box (same format as the real location_options query)
PLACES = {
    "City | Gaya (Bihar)": (24.7914, 85.0002, "Approximate city centre (median of PIN centres in the district)", "City centre, approximate"),
    "City | Patna (Bihar)": (25.5941, 85.1376, "Approximate city centre (median of PIN centres in the district)", "City centre, approximate"),
    "City | Lucknow (Uttar Pradesh)": (26.8467, 80.9462, "Approximate city centre (median of PIN centres in the district)", "City centre, approximate"),
    "PIN 823001 | Gaya (Bihar)": (24.7955, 85.0077, "Centre of PIN 823001", "PIN centre"),
    "PIN 800001 | Patna (Bihar)": (25.6093, 85.1235, "Centre of PIN 800001", "PIN centre"),
}

# Invented sample facilities: (id, name, lat, lon, address, phone, website, specialties, procedures, equipment)
FACILITIES = [
    ("mock-01", "Sample City Hospital", 24.7990, 85.0100, "Station Road, Gaya, Bihar 823001", "+91 631 000 0001", "www.example.org/city",
     ["Nephrology", "Cardiology", "Obstetrics and gynecology", "Neonatology", "Radiology", "General surgery"],
     ["Hemodialysis", "Caesarean section", "Coronary angiography", "MRI brain", "Burn dressing"],
     ["Hemodialysis machine (6)", "1.5 Tesla MRI scanner", "Neonatal incubator", "Ventilator", "Cath lab"]),
    ("mock-02", "Sample Kidney and Heart Centre", 24.7860, 85.0230, "Civil Lines, Gaya, Bihar 823001", "+91 631 000 0002", "www.example.org/khc",
     ["Nephrology", "Cardiothoracic surgery", "Critical care"],
     ["Hemodialysis", "CAPD", "CABG", "Valve replacement"],
     ["Dialysis machines (12)", "Heart-lung machine", "ICU beds"]),
    ("mock-03", "Sample Mother and Child Clinic", 24.7700, 84.9900, "AP Colony, Gaya, Bihar 823001", "+91 631 000 0003", None,
     ["Obstetrics and gynecology", "Pediatrics", "Neonatology"],
     ["Normal delivery", "Emergency caesarean section", "Newborn resuscitation"],
     ["NICU with 10 beds", "Phototherapy unit", "Fetal monitor"]),
    ("mock-04", "Sample Diagnostic Centre", 24.8120, 84.9850, "Bodh Gaya Road, Gaya, Bihar 824231", "+91 631 000 0004", "www.example.org/diag",
     ["Radiology", "Pathology"],
     ["MRI scan", "CT scan", "Ultrasound"],
     ["3 Tesla MRI", "128-slice CT"]),
    ("mock-05", "Sample District Hospital", 24.7500, 85.0400, "Hospital Road, Gaya, Bihar 823001", "+91 631 000 0005", "www.example.org/dh",
     ["General medicine", "General surgery", "Obstetrics and gynecology", "Burns and plastic surgery"],
     ["Burn wound care", "Skin grafting", "Caesarean section", "Dialysis (planned)"],
     ["Burn unit", "Operation theatre"]),
    ("mock-06", "Sample Multispeciality Hospital", 24.8300, 85.0500, "Manpur, Gaya, Bihar 823003", "+91 631 000 0006", "www.example.org/msh",
     ["Nephrology", "Cardiology", "Neurology", "Radiology"],
     ["Dialysis", "Angioplasty", "MRI spine"],
     ["Dialysis unit", "MRI machine", "Cath lab"]),
    ("mock-07", "Sample Heart Institute Patna", 25.6000, 85.1300, "Bailey Road, Patna, Bihar 800001", "+91 612 000 0007", "www.example.org/hip",
     ["Cardiothoracic surgery", "Cardiology"], ["CABG", "Open heart surgery", "Pediatric cardiac surgery"], ["Heart-lung machine", "Cardiac ICU"]),
    ("mock-08", "Sample Newborn Care Unit Patna", 25.6100, 85.1000, "Kankarbagh, Patna, Bihar 800020", "+91 612 000 0008", None,
     ["Neonatology", "Pediatrics"], ["Neonatal intensive care", "Surfactant therapy"], ["NICU with 20 beds", "Neonatal ventilator"]),
]

# Words that count as evidence for each service (mirrors the real keyword idea, simplified)
TERMS = {
    "Dialysis": r"dialysis|hemodialysis|capd|nephrolog",
    "Emergency C-section": r"caesarean|cesarean|c-section",
    "NICU": r"nicu|neonat|newborn",
    "Cardiac surgery": r"cabg|cardiothoracic|heart surgery|open heart|valve|cardiac surgery|heart-lung",
    "MRI": r"\bmri\b|tesla",
    "Burn care": r"burn",
}
SYNONYMS = {
    "kidney": "Dialysis", "dialysis": "Dialysis", "renal": "Dialysis", "gurde": "Dialysis",
    "caesarean": "Emergency C-section", "c-section": "Emergency C-section", "delivery": "Emergency C-section",
    "nicu": "NICU", "newborn": "NICU", "baby": "NICU", "premature": "NICU",
    "heart": "Cardiac surgery", "cardiac": "Cardiac surgery", "dil": "Cardiac surgery", "chest pain": "Cardiac surgery",
    "mri": "MRI", "scan": "MRI", "burn": "Burn care", "hot oil": "Burn care", "fire": "Burn care",
}


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _hits(fac, service):
    pat = re.compile(TERMS[service], re.I)
    fields = {"specialties": fac[7], "procedure": fac[8], "equipment": fac[9]}
    return {k: [x for x in v if pat.search(x)] for k, v in fields.items()}


def _suggest(text):
    t = (text or "").strip().lower()
    cols = ["suggestion_rank", "service", "level", "reason", "method", "is_selected", "selection_note"]
    exact = [s for s in SERVICES if s.lower() == t]
    if exact:
        return pd.DataFrame([[1, exact[0], "Exact name", f"Matched the service name ({exact[0]})",
                              "Name match (no LLM)", True, f"Searching for: {exact[0]}"]], columns=cols)
    found = []
    for word, svc in SYNONYMS.items():
        if word in t and svc not in found:
            found.append(svc)
    if not found:
        return pd.DataFrame([[None, None, None, None, None, False,
                              "No supported service matched. Try: " + ", ".join(SERVICES) + "."]], columns=cols)
    level = "High" if len(found) == 1 and len(t.split()) <= 6 else "Medium"
    rows = []
    for i, svc in enumerate(found[:3], 1):
        sel = i == 1 and level == "High"
        rows.append([i, svc, level if i == 1 else "Medium", f'(mock) words in the request suggest {svc.lower()}',
                     "LLM suggestion (Llama 3.3 70B). Level is an estimate by the model, not a probability. [MOCK]",
                     sel, f"Searching for: {found[0]}" if level == "High" else
                     "Not confident enough to choose. Type one of the suggested services in the box to search."])
    return pd.DataFrame(rows, columns=cols)


def _resolve(text):
    cols = ["origin_latitude", "origin_longitude", "location_note", "origin_precision"]
    p = PLACES.get(text)
    if not p:  # also accept a bare city name or PIN
        for k, v in PLACES.items():
            if text and text.lower() in k.lower():
                p = v
                break
    if not p:
        return pd.DataFrame([[None, None, f"Place not found: {text}", None]], columns=cols)
    return pd.DataFrame([[p[0], p[1], p[2] + " [MOCK]", p[3]]], columns=cols)


def _origin(location_text, coords_text):
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)", coords_text or "")
    if m:
        return float(m.group(1)), float(m.group(2))
    r = _resolve(location_text)
    return (r.iloc[0]["origin_latitude"], r.iloc[0]["origin_longitude"]) if pd.notna(r.iloc[0]["origin_latitude"]) else None


def _find(service, location_text, coords_text):
    origin = _origin(location_text, coords_text)
    rows = []
    for f in FACILITIES:
        h = _hits(f, service)
        n_fields = sum(1 for v in h.values() if v)
        if not n_fields:
            continue
        planned = any("planned" in x.lower() for v in h.values() for x in v)
        parts = []
        if h["specialties"]:
            parts.append("+1 capability")
        if h["procedure"]:
            parts.append("+1 procedure")
        if h["equipment"]:
            parts.append("+1 equipment")
        if n_fields >= 2:
            parts.append("+1 mentioned in 2 or more fields")
        if f[6]:
            parts.append("+1 source link")
        if planned:
            parts.append("-2 wording suggests planned")
        score = max(0, min(5, sum(int(p.split()[0]) for p in parts)))
        label = "Stronger" if score >= 4 else "Moderate" if score >= 2 else "Weak"
        dist = _km(origin, (f[2], f[3])) if origin else 0.0
        quote = next(x for k in ("procedure", "equipment", "specialties") for x in h[k])
        rows.append({
            "source_record_id": f[0], "facility_id": f[0], "facility_name": f[1], "full_address": f[4],
            "latitude": f[2], "longitude": f[3], "distance_km": round(dist, 2),
            "distance_label": f"{dist:.1f} km straight-line",
            "evidence_strength": score, "strength_label": f"{label} text evidence",
            "strength_breakdown": ", ".join(parts),
            "caution_flags": "Wording suggests planned, not running" if planned else None,
            "evidence_fields": ", ".join(k for k, v in h.items() if v),
            "evidence_quote": quote, "source_url": f[6], "website": f[6], "phone": f[5],
            "why_this": f"{dist:.1f} km away; {label.lower()} text evidence ({score}/5). [MOCK DATA]",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("distance_km").head(20).reset_index(drop=True)
    df["nearest_rank"] = range(1, len(df) + 1)
    return df


def _extras(record_id):
    f = next((x for x in FACILITIES if x[0] == record_id), None)
    if not f:
        return pd.DataFrame()
    return pd.DataFrame([{"specialties": json.dumps(f[7]), "procedure": json.dumps(f[8]),
                          "equipment": json.dumps(f[9]), "raw_unique_id": f[0]}])


def _points(service):
    rows = [{"facility_id": f[0], "facility_name": f[1], "latitude": f[2], "longitude": f[3]}
            for f in FACILITIES if any(_hits(f, service).values())]
    return pd.DataFrame(rows, columns=["facility_id", "facility_name", "latitude", "longitude"])


def run_query(query: str, params: dict | None = None) -> pd.DataFrame:
    p = params or {}
    q = " ".join(query.split())
    if "suggest_services(" in q:
        return _suggest(p.get("t"))
    if "resolve_location_v2(" in q:
        return _resolve(p.get("t"))
    if "find_care(" in q:
        return _find(p.get("s"), p.get("l"), p.get("c"))
    if "facility_records r" in q:
        return _extras(p.get("id"))
    if "max(f.email)" in q:
        f = next((x for x in FACILITIES if x[0] == p.get("u")), None)
        return pd.DataFrame([{"email": f"info@{f[0]}.example.org" if f else None}])
    if "service_candidates c" in q:
        return _points(p.get("s"))
    if "city_origins" in q:
        return pd.DataFrame({"label": list(PLACES)})
    raise ValueError("mock_backend has no answer for this query: " + q[:120])
