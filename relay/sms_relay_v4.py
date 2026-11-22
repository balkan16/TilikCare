"""
CareMatch SMS relay (v4: v3 + confirm step when the classifier is not High confidence).

Flow:
  Health worker SMS -> Android gateway (SMS Gateway for Android, Local or Cloud mode)
  -> webhook (HTTPS via cloudflared) -> this relay
  -> Databricks (team workspace):
       1. service_classifier_cache / classify_service(need)   lookup -> embedding -> LLM
       2. resolve_location_v2(place)                         PIN or city
       3. find_care(service, place, 'none')                  the SAME function the web app uses
  -> reply SMS (<=160 chars) via the gateway.

Accepted messages (must start with CARE):
  CARE
  Need: patient has kidney failure, legs swollen
  PIN: 823001
  Area: Gaya
  Urgent: no
or one line:  CARE dialysis 823001   /   CARE dialysis Gaya

Confirm step (v4): if the classifier is only Medium sure, the relay asks
  "Did you mean HEART SURGERY near Gaya? Reply YES ..."
and searches only after the same phone replies YES (or HAAN) within 15 minutes.
Same rule as the web app, which only auto-searches High-confidence matches.

Environment variables (set in Terminal, never in this file):
  DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID   (team workspace)
  SMSGATE_USER, SMSGATE_PASS, SMSGATE_URL (Local mode: http://<phone-ip>:8080)
Optional: CAREMATCH_SCHEMA (default workspace.carematch)
Uses only the Python standard library.
"""
import base64, json, os, re, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
WH = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SCHEMA = os.environ.get("CAREMATCH_SCHEMA", "workspace.carematch")
SG_USER = os.environ.get("SMSGATE_USER", "")
SG_PASS = os.environ.get("SMSGATE_PASS", "")
SG_BASE = os.environ.get("SMSGATE_URL", "https://api.sms-gate.app/3rdparty/v1").rstrip("/")
SG_API = SG_BASE + "/message"
PORT = 8787
KEYWORD = "care"

TEMPLATE = "Send:\nCARE\nNeed: <problem or service>\nPIN: <6 digits>\nArea: <city>\nExample: CARE dialysis 823001"
NOT_COVERED = ("CareMatch covers dialysis, C-section, NICU, heart surgery, MRI, burns. "
               "Not covered: visit nearest health centre. Emergency: call 108.")
SHORT = {"Dialysis": "DIALYSIS", "Emergency C-section": "C-SECTION", "NICU": "NICU",
         "Cardiac surgery": "HEART SURGERY", "MRI": "MRI", "Burn care": "BURN CARE"}
URGENT_WORDS = r"\b(urgent|emergency|unconscious|not breathing|heavy bleeding|bleeding a lot|fits|seizure|accident)\b"

seen = set()
pending = {}          # sender -> {"service", "place", "urgent", "ts"}; waiting for YES
PENDING_TTL = 15 * 60  # seconds
YES_WORDS = {"yes", "y", "ya", "haan", "han", "ha", "ok", "care yes", "care haan"}


# ------------------------------------------------------------------------------------------
# Databricks
# ------------------------------------------------------------------------------------------
def http_json(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "CareMatch-Relay/4.0 (datathon test)", **(headers or {})})
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def run_sql(sql, params=None):
    """Run one statement with named STRING parameters (:name). Returns a list of dict rows."""
    auth = {"Authorization": f"Bearer {TOKEN}"}
    body = {"warehouse_id": WH, "statement": sql, "wait_timeout": "30s", "on_wait_timeout": "CONTINUE",
            "parameters": [{"name": k, "value": v, "type": "STRING"} for k, v in (params or {}).items()]}
    res = http_json(HOST + "/api/2.0/sql/statements", "POST", body, auth)
    sid, t0 = res.get("statement_id"), time.time()
    while res["status"]["state"] in ("PENDING", "RUNNING"):
        if time.time() - t0 > 120:
            raise RuntimeError("query timed out")
        time.sleep(1)
        res = http_json(f"{HOST}/api/2.0/sql/statements/{sid}", headers=auth)
    if res["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(str(res["status"])[:300])
    cols = [c["name"] for c in (res.get("manifest", {}).get("schema", {}).get("columns") or [])]
    return [dict(zip(cols, r)) for r in (res.get("result", {}).get("data_array") or [])]


def classify(need):
    """Cached service classification. Same phrase -> same answer."""
    norm = re.sub(r"\s+", " ", need.lower()).strip()
    hit = run_sql(f"SELECT service, method, confidence FROM {SCHEMA}.service_classifier_cache "
                  f"WHERE input_norm = :n ORDER BY created_at DESC LIMIT 1", {"n": norm})
    if hit:
        return hit[0]["service"], hit[0]["method"] + " (cached)", hit[0]["confidence"]
    rows = run_sql(f"SELECT service, method, confidence, CAST(similarity AS STRING) AS sim, note "
                   f"FROM {SCHEMA}.classify_service(:t)", {"t": need})
    r = rows[0] if rows else {"service": None, "method": "none", "confidence": "None", "sim": None, "note": ""}
    try:  # cache write failures must never block the reply
        run_sql(f"INSERT INTO {SCHEMA}.service_classifier_cache VALUES "
                f"(:n, nullif(:s, ''), :m, :c, CAST(nullif(:sim, '') AS DOUBLE), :note, current_timestamp())",
                {"n": norm, "s": r["service"] or "", "m": r["method"], "c": r["confidence"],
                 "sim": r.get("sim") or "", "note": r.get("note") or ""})
    except Exception as e:
        print("   cache write skipped:", e)
    return r["service"], r["method"], r["confidence"]


def resolve_place(place):
    rows = run_sql(f"SELECT place_label, origin_latitude FROM {SCHEMA}.resolve_location_fast(:p)", {"p": place})
    return rows[0] if rows and rows[0].get("origin_latitude") is not None else None


def score4(breakdown):
    """Evidence score 0-4, same as the web app: the source-link point is not evidence of the service."""
    parts = [p.strip() for p in str(breakdown or "").split(", ") if p.strip() and "source link" not in p.lower()]
    total = sum(int(m.group(1)) for m in (re.match(r"^([+-]\d+)", p) for p in parts) if m)
    return max(0, min(4, total))


def find_care(service, place):
    # find_care_fast = same results as find_care, but reads stored tables (about 2 s instead of 8-12 s).
    # It expects one of the 6 exact service names, which classify() already returns.
    rows = run_sql(f"SELECT facility_name, distance_km, strength_breakdown, phone "
                   f"FROM {SCHEMA}.find_care_fast(:s, :l, 'none') ORDER BY nearest_rank LIMIT 2",
                   {"s": service, "l": place})
    for r in rows:
        r["evidence_strength"] = score4(r.get("strength_breakdown"))
    return rows


# ------------------------------------------------------------------------------------------
# Message parsing and reply
# ------------------------------------------------------------------------------------------
def parse(text):
    """Return dict(need, pin, area, urgent) from the template or the one-line form."""
    body = re.sub(r"^\s*care\b[:\s]*", "", text, flags=re.I).strip()
    fields = {}
    for line in re.split(r"[\n;]+", body):
        m = re.match(r"\s*(need|service|problem|pin|pincode|area|place|city|district|urgent)\s*[:=-]\s*(.*)", line, re.I)
        if m:
            key = m.group(1).lower()
            key = {"service": "need", "problem": "need", "pincode": "pin", "place": "area",
                   "city": "area", "district": "area"}.get(key, key)
            fields[key] = m.group(2).strip()
    if not fields:  # one-line form: CARE <need> <PIN or city>
        pin = re.search(r"\b(\d{6})\b", body)
        rest = re.sub(r"\b\d{6}\b", " ", body).strip(" ,")
        fields = {"need": rest, "pin": pin.group(1) if pin else ""}
        if not pin and len(rest.split()) >= 2:  # last word may be a city
            fields["area_guess"] = rest.split()[-1]
    pin = re.search(r"\b(\d{6})\b", fields.get("pin", "") or "")
    area = (fields.get("area") or "").split(",")[0].strip()  # "Gaya, Bihar" -> "Gaya"
    need = (fields.get("need") or "").strip()
    urgent = bool(re.match(r"\s*y", fields.get("urgent", ""), re.I)) or bool(re.search(URGENT_WORDS, need, re.I))
    return {"need": need, "pin": pin.group(1) if pin else "",
            "area": area, "area_guess": fields.get("area_guess", ""), "urgent": urgent}


def short_name(name, n=16):
    name = re.sub(r"\b(hospitals?)\b", "Hosp", name or "", flags=re.I)
    name = re.sub(r"\b(super ?speciality|multi ?speciality|and research (institute|centre|center))\b", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" ,-&")[:n]


def results_reply(service, place, urgent, read_as=""):
    rows = find_care(service, place)
    if not rows:
        return f"No {SHORT[service].lower()} found near {place} in our data. Ask your coordinator. Emergency: 108."
    lines = [("Call 108 now. " if urgent else "") + f"{read_as}{SHORT[service]} {place}:"]
    for i, r in enumerate(rows, 1):
        phone = (r.get("phone") or "no tel").replace("+91", "0").replace(" ", "")
        km = round(float(r["distance_km"]))
        lines.append(f"{i}){short_name(r['facility_name'])} {km}km ev{r['evidence_strength']}/4 {phone}")
    lines.append("Unverified. Call first.")
    reply = "\n".join(lines)
    while len(reply) > 160 and len(lines) > 3:  # drop option 2 if too long
        lines.pop(-2)
        reply = "\n".join(lines)
    return reply[:160]


def is_yes(text):
    return re.sub(r"[^a-z ]", "", text.lower()).strip() in YES_WORDS


def confirm_reply(sender):
    """Called when a sender replies YES. Runs the search they were asked to confirm."""
    p = pending.pop(sender, None)
    if not p or time.time() - p["ts"] > PENDING_TTL:
        return "Nothing to confirm (or it expired). " + TEMPLATE
    return results_reply(p["service"], p["place"], p["urgent"], "Confirmed ")


def build_reply(text, sender=""):
    f = parse(text)
    if not f["need"] or not (f["pin"] or f["area"] or f["area_guess"]):
        return TEMPLATE

    # 1. location first: PIN, then Area, then a guessed last word of the one-line form
    place = None
    for cand in [f["pin"], f["area"], f["area_guess"]]:
        if cand:
            loc = resolve_place(cand)
            if loc:
                place = cand
                if cand == f["area_guess"]:  # remove the city word from the need text
                    f["need"] = f["need"].rsplit(" ", 1)[0]
                break
    if not place:
        return "Place not found. Send a 6-digit PIN or only the city name, e.g. Area: Gaya"

    # 2. service: cached classifier (lookup -> embedding -> LLM)
    service, method, conf = classify(f["need"])
    print(f"   need -> {service} via {method} ({conf}), place -> {place}")
    if not service:
        return NOT_COVERED

    pending.pop(sender, None)  # a new request replaces any unanswered question
    if conf != "High":  # not sure enough: ask first, search only after YES
        pending[sender] = {"service": service, "place": place, "urgent": f["urgent"], "ts": time.time()}
        return (("Call 108 now. " if f["urgent"] else "") +
                f"Did you mean {SHORT[service]} near {place}? Reply YES to search, or send CARE with a clearer need.")[:160]

    read_as = "" if method.startswith("lookup") else "Read as "
    return results_reply(service, place, f["urgent"], read_as)


# ------------------------------------------------------------------------------------------
# SMS gateway
# ------------------------------------------------------------------------------------------
def send_sms(to, text):
    auth = {"Authorization": "Basic " + base64.b64encode(f"{SG_USER}:{SG_PASS}".encode()).decode()}
    res = http_json(SG_API, "POST", {"message": text, "phoneNumbers": [to]}, auth)
    mid = res.get("id")
    print(f"   gateway accepted, id={mid}, recipient={to[:3]}...{to[-4:]}")

    def check():
        for wait in (5, 15):
            time.sleep(wait)
            try:
                st = http_json(f"{SG_API}/{mid}", headers=auth)
                rec = (st.get("recipients") or [{}])[0]
                print(f"   status after ~{wait}s: {st.get('state')} {rec.get('error') or ''}")
                if st.get("state") in ("Delivered", "Failed"):
                    return
            except Exception as e:
                print("   status check error:", e)
                return
    threading.Thread(target=check, daemon=True).start()


def handle(sender, text):
    t0 = time.time()
    try:
        reply = confirm_reply(sender) if is_yes(text) else build_reply(text, sender)
    except Exception as e:  # never leave the user without an answer
        print("engine error:", e)
        reply = "CareMatch: system busy, please try again in 1 minute. Emergency: 108."
    try:
        send_sms(sender, reply)
        print(f"-> replied to {sender[-4:].rjust(len(sender), '*')} in {time.time() - t0:.1f}s:\n{reply}\n")
    except urllib.error.HTTPError as e:
        print("send error:", e.code, e.read().decode()[:300])
    except Exception as e:
        print("send error:", e)


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return
        if data.get("event", "") not in ("sms:received", "mms:received"):
            return
        p = data.get("payload", {}) or {}
        mid = p.get("messageId") or p.get("id")
        sender = p.get("sender") or p.get("phoneNumber") or ""
        text = (p.get("message") or p.get("text") or p.get("body") or "").strip()
        wanted = text.lower().startswith(KEYWORD) or (is_yes(text) and sender in pending)
        if mid in seen or not wanted:
            return
        seen.add(mid)
        print(f"<- SMS from {sender[-4:].rjust(len(sender), '*')}: {text!r}")
        threading.Thread(target=handle, args=(sender, text), daemon=True).start()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    missing = [k for k, v in {"DATABRICKS_HOST": HOST, "DATABRICKS_TOKEN": TOKEN, "DATABRICKS_WAREHOUSE_ID": WH,
                              "SMSGATE_USER": SG_USER, "SMSGATE_PASS": SG_PASS}.items() if not v]
    if missing:
        raise SystemExit(f"Set these environment variables first: {', '.join(missing)}")
    print(f"Databricks: {HOST}  schema: {SCHEMA}")
    print(f"Gateway API: {SG_BASE}")
    print(f"CareMatch relay v4 listening on http://127.0.0.1:{PORT}/sms  (only messages starting with 'CARE')")
    HTTPServer(("127.0.0.1", PORT), Hook).serve_forever()
