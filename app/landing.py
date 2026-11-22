"""TilikCare landing page (shown before the dashboard). Pure HTML/CSS via st.html; no scripts.

The "Find a facility" buttons link to ?view=dashboard, which app.py reads to show the tool.
"""
import base64
import json
import os

import streamlit as st

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CORAL, NAVY, CREAM, PEACH = "#ee5a3f", "#1a2233", "#fbf7f3", "#fce5dd"


@st.cache_resource(show_spinner=False)
def india_svg_paths() -> str:
    """State outlines as SVG paths (simple equirectangular projection), for the hero illustration."""
    with open(os.path.join(APP_DIR, "india_states.geojson")) as f:
        geo = json.load(f)
    x0, x1, y0, y1 = 68.0, 97.5, 6.5, 37.2

    def pt(lon, lat):
        return f"{(lon - x0) / (x1 - x0) * 300:.1f},{(y1 - lat) / (y1 - y0) * 330:.1f}"

    out = []
    for feat in geo["features"]:
        g = feat["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        d = "".join("M" + "L".join(pt(*c) for c in poly[0][::2]) + "Z" for poly in polys if len(poly[0]) > 6)
        hot = feat["properties"]["name"] == "Bihar"
        style = "fill:#fce5dd;stroke:#ee5a3f;stroke-width:1.4" if hot else "fill:#ffffff;stroke:#d6c4ba;stroke-width:.6"
        out.append(f"<path d='{d}' style='{style}'/>")
    return svg_img("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 300 330'>" + "".join(out) + "</svg>")


def svg_img(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


LOGO_SVG = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 44' width='38' height='35'><path d='M24 40 C10 30 3 22 3 13.5 C3 7.5 7.8 3 13.6 3 "
        f"C18 3 21.6 5.6 24 9.4 C26.4 5.6 30 3 34.4 3 C40.2 3 45 7.5 45 13.5 C45 22 38 30 24 40 Z' fill='none' stroke='{CORAL}' "
        f"stroke-width='4.2' stroke-linejoin='round'/><path d='M21 14h6v5h5v6h-5v5h-6v-5h-5v-6h5z' fill='{CORAL}'/></svg>")
LOGO = f"<img src='{svg_img(LOGO_SVG)}' width='38' height='35' alt=''>"
PIN_ICON = svg_img(f"<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24'><path d='M12 22s7-6.3 7-12a7 7 0 1 0-14 0c0 5.7 7 12 7 12z' "
                   f"fill='none' stroke='{CORAL}' stroke-width='2'/><circle cx='12' cy='10' r='2.6' fill='none' stroke='{CORAL}' stroke-width='2'/></svg>")
DASH = "?view=dashboard"


def render_landing():
    st.html(f"""
<style>
  header[data-testid='stHeader'], [data-testid='stToolbar'] {{ display:none; }}
  div.block-container {{ padding:0 !important; max-width:100% !important; }}
  .tl {{ font-family:'Inter','Source Sans Pro',-apple-system,'Segoe UI',sans-serif; color:{NAVY}; background:{CREAM}; }}
  .tl a {{ text-decoration:none; }}
  .tl .wrap {{ max-width:1240px; margin:0 auto; padding:0 40px; }}
  .tl nav {{ display:flex; align-items:center; justify-content:space-between; padding:26px 0; }}
  .tl .brand {{ display:flex; align-items:center; gap:10px; }}
  .tl .wm {{ font-size:27px; font-weight:800; letter-spacing:-.4px; line-height:1; color:{NAVY}; }}
  .tl .wm b {{ color:{CORAL}; }}
  .tl .tg {{ font-size:12px; font-weight:600; margin-top:3px; }}
  .tl .links {{ display:flex; gap:38px; font-size:16px; font-weight:600; }}
  .tl .links a {{ color:{NAVY}; }} .tl .links a:hover {{ color:{CORAL}; }}
  .tl .btn {{ display:inline-flex; align-items:center; gap:10px; background:{CORAL}; color:#fff !important; font-weight:700;
             font-size:16px; padding:15px 26px; border-radius:10px; box-shadow:0 6px 18px rgba(238,90,63,.28); }}
  .tl .btn:hover {{ background:#d9472d; }}
  .tl .btn.ghost {{ background:transparent; color:{NAVY} !important; border:2px solid {NAVY}; box-shadow:none; }}
  .tl .hero {{ display:grid; grid-template-columns:1.05fr 1fr; gap:50px; align-items:center; padding:70px 0 60px; }}
  .tl .eyebrow {{ color:{CORAL}; font-weight:800; letter-spacing:3px; font-size:14px; }}
  .tl h1 {{ font-size:62px; line-height:1.02; letter-spacing:-1.5px; margin:18px 0 22px; font-weight:900; color:{NAVY}; text-transform:none; }}
  .tl h1 em {{ color:{CORAL}; font-style:italic; font-weight:800; text-transform:none; letter-spacing:-1px; }}
  .tl h2 em {{ color:{CORAL}; font-style:italic; }}
  .tl h1 .hl {{ color:{CORAL}; }}
  .tl h1 .mark {{ background:{PEACH}; color:{CORAL}; border-radius:14px; padding:0 12px; }}
  .tl .lead {{ font-size:20px; line-height:1.55; color:#4b5563; max-width:560px; }}
  .tl .bar {{ width:64px; height:5px; background:{CORAL}; border-radius:3px; margin:26px 0; }}
  .tl .cta {{ display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; }}
  .tl .loc {{ display:inline-flex; align-items:center; gap:10px; margin-top:30px; background:{PEACH}; border-radius:10px;
             padding:9px 16px; font-weight:700; font-size:15px; }}
  /* product illustration */
  .tl .shot {{ background:#fff; border-radius:18px; box-shadow:0 24px 60px rgba(26,34,51,.18); overflow:hidden; border:1px solid #eadfd8; }}
  .tl .shot .top {{ display:flex; gap:8px; align-items:center; padding:12px 16px; border-bottom:1px solid #f0e6e0; }}
  .tl .shot .sb {{ flex:1; display:flex; background:#f3e9e3; border-radius:999px; font-size:12px; }}
  .tl .shot .sb div {{ flex:1; padding:7px 14px; }} .tl .shot .sb b {{ display:block; font-size:10.5px; }}
  .tl .shot .sb .go {{ flex:0; background:{CORAL}; color:#fff; border-radius:999px; margin:4px; padding:6px 14px; font-weight:700; white-space:nowrap; }}
  .tl .shot .body {{ display:grid; grid-template-columns:1fr 1.1fr; min-height:360px; }}
  .tl .shot .cards {{ padding:14px; display:flex; flex-direction:column; gap:10px; background:{CREAM}; }}
  .tl .shot .c {{ background:#fff; border:1px solid #eadfd8; border-radius:12px; padding:10px 12px; font-size:12px; }}
  .tl .shot .c.on {{ border:2px solid {NAVY}; box-shadow:0 6px 16px rgba(0,0,0,.1); }}
  .tl .shot .c b {{ font-size:13.5px; display:block; margin:2px 0; }}
  .tl .shot .chip {{ display:inline-block; background:#dcfce7; color:#166534; border-radius:999px; padding:1px 8px; font-weight:700; font-size:11px; }}
  .tl .shot .chip.o {{ background:#ffedd5; color:#9a3412; }}
  .tl .shot .ok {{ color:#15803d; }} .tl .shot .no {{ color:#9ca3af; }}
  .tl .shot .map {{ position:relative; background:#f6efe9; }}
  .tl .shot img.india {{ position:absolute; inset:16px; width:calc(100% - 32px); height:calc(100% - 32px); object-fit:contain; }}
  .tl .st {{ fill:#fff; stroke:#d6c4ba; stroke-width:.6; }} .tl .st.hot {{ fill:{PEACH}; stroke:{CORAL}; stroke-width:1.4; }}
  .tl .pp {{ position:absolute; background:#fff; border:2px solid #16a34a; border-radius:999px; padding:2px 8px; font-size:11px; font-weight:800;
            box-shadow:0 2px 6px rgba(0,0,0,.2); }}
  .tl .pp.sel {{ background:{NAVY}; color:#fff; border-color:{NAVY}; }} .tl .pp.o {{ border-color:#ea580c; }}
  .tl .me {{ position:absolute; width:12px; height:12px; background:#2563eb; border:3px solid #fff; border-radius:50%; box-shadow:0 0 0 2px #2563eb; }}
  .tl .strip {{ display:flex; align-items:center; gap:24px; border-top:1px solid #eadfd8; border-bottom:1px solid #eadfd8; padding:18px 0; margin-bottom:20px; overflow:hidden; }}
  .tl .strip .lbl {{ font-weight:800; letter-spacing:2px; font-size:12px; color:#9a8a82; white-space:nowrap; }}
  .tl .mq {{ overflow:hidden; flex:1; mask-image:linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent); }}
  .tl .mqi {{ display:flex; gap:48px; width:max-content; animation:mq 32s linear infinite; }}
  .tl .mqi span {{ font-weight:700; color:#6b5d56; font-size:17px; white-space:nowrap; }}
  @keyframes mq {{ from {{ transform:translateX(0); }} to {{ transform:translateX(-50%); }} }}
  /* sections */
  .tl .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:18px; padding:10px 0 70px; }}
  .tl .stat {{ background:#fff; border:1px solid #eadfd8; border-radius:16px; padding:22px; }}
  .tl .stat .n {{ font-size:34px; font-weight:800; color:{CORAL}; letter-spacing:-1px; }} .tl .stat .l {{ color:#4b5563; font-size:14.5px; margin-top:4px; }}
  .tl section {{ padding:70px 0; }} .tl section.alt {{ background:#fff; }}
  .tl h2 {{ font-size:40px; letter-spacing:-1px; margin:10px 0 14px; font-weight:800; }}
  .tl .sub {{ color:#4b5563; font-size:18px; max-width:720px; line-height:1.55; }}
  .tl .flow {{ display:grid; grid-template-columns:repeat(5,1fr); gap:16px; margin-top:36px; }}
  .tl .fs {{ background:{CREAM}; border:1px solid #eadfd8; border-radius:18px; padding:22px 18px; position:relative; }}
  .tl .fb {{ height:4px; border-radius:2px; background:linear-gradient(90deg,{CORAL},#f7a08f); margin-bottom:18px; }}
  .tl .fs h3 {{ font-size:19px; margin:14px 0 6px; }} .tl .fs p {{ color:#4b5563; font-size:14.5px; line-height:1.5; margin:0; }}
  .tl .steps {{ display:grid; grid-template-columns:repeat(3,1fr); gap:22px; margin-top:36px; }}
  .tl .step {{ background:{CREAM}; border-radius:18px; padding:26px; border:1px solid #eadfd8; }}
  .tl section.alt .step {{ background:{CREAM}; }}
  .tl .num {{ width:38px; height:38px; border-radius:50%; background:{CORAL}; color:#fff; display:flex; align-items:center; justify-content:center; font-weight:800; }}
  .tl .step h3 {{ font-size:20px; margin:16px 0 8px; }} .tl .step p {{ color:#4b5563; line-height:1.55; margin:0; font-size:15.5px; }}
  .tl .ex {{ margin-top:12px; background:#fff; border-radius:10px; padding:8px 12px; font-family:ui-monospace,Menlo,monospace; font-size:13px; border:1px dashed #d6c4ba; }}
  .tl .two {{ display:grid; grid-template-columns:1fr 1fr; gap:40px; align-items:center; margin-top:30px; }}
  .tl .cl {{ background:#fff; border:1px solid #eadfd8; border-radius:16px; overflow:hidden; box-shadow:0 12px 30px rgba(26,34,51,.08); }}
  .tl .cl .h {{ padding:14px 18px; font-weight:800; border-bottom:1px solid #f0e6e0; }}
  .tl .cl .r {{ display:flex; gap:12px; padding:12px 18px; border-top:1px solid #f4ece7; font-size:14.5px; }}
  .tl .cl .i {{ width:24px; height:24px; border-radius:50%; flex:0 0 24px; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:13px; }}
  .tl .cl .y .i {{ background:#dcfce7; color:#15803d; }} .tl .cl .n .i {{ background:#f3f4f6; color:#9ca3af; }}
  .tl .cl .e {{ color:#15803d; font-size:13px; margin-top:2px; }} .tl .cl .n .t {{ color:#6b7280; }}
  .tl ul.ticks {{ list-style:none; padding:0; margin:18px 0 0; }} .tl ul.ticks li {{ padding:8px 0 8px 32px; position:relative; font-size:16.5px; line-height:1.5; }}
  .tl ul.ticks li:before {{ content:'✓'; position:absolute; left:0; top:8px; width:22px; height:22px; border-radius:50%; background:{PEACH};
                           color:{CORAL}; font-weight:800; font-size:13px; display:flex; align-items:center; justify-content:center; }}
  .tl .chan {{ display:grid; grid-template-columns:1fr 1fr; gap:22px; margin-top:34px; }}
  .tl .ch {{ border-radius:18px; padding:30px; border:1px solid #eadfd8; background:{CREAM}; }}
  .tl .ch h3 {{ font-size:22px; margin:8px 0 10px; }} .tl .ch p {{ color:#4b5563; line-height:1.55; }}
  .tl .tag {{ display:inline-block; background:{PEACH}; color:{CORAL}; font-weight:800; font-size:12px; letter-spacing:1.5px; border-radius:999px; padding:4px 12px; }}
  .tl .sms {{ background:{NAVY}; color:#fff; border-radius:14px; padding:14px 16px; font-family:ui-monospace,Menlo,monospace; font-size:13px; line-height:1.5; margin-top:12px; }}
  .tl .sms .in {{ color:#fca5a5; }}
  .tl .mgrid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
  .tl .m {{ background:{CREAM}; border:1px solid #eadfd8; border-radius:16px; padding:20px; }}
  .tl .m .n {{ font-size:38px; font-weight:900; color:{CORAL}; letter-spacing:-1px; }} .tl .m .l {{ color:#4b5563; font-size:14px; margin-top:4px; }}
  .tl .ch.dark {{ background:{NAVY}; color:#fff; border-color:{NAVY}; }} .tl .ch.dark p {{ color:#cbd5e1; }} .tl .ch.dark h3 {{ color:#fff; }}
  .tl .btn.ghost.light {{ color:#fff !important; border-color:#fff; }}
  .tl .limits {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-top:28px; }}
  .tl .lim {{ background:#fff; border-radius:14px; padding:18px; border:1px solid #eadfd8; font-size:15px; line-height:1.5; color:#374151; }}
  .tl .lim b {{ display:block; color:{NAVY}; margin-bottom:4px; }}
  .tl .final {{ background:{NAVY}; color:#fff; border-radius:24px; padding:50px; display:flex; justify-content:space-between; align-items:center; gap:30px; margin:20px 0 60px; }}
  .tl .final h2 {{ color:#fff; margin:0 0 8px; }} .tl .final p {{ color:#cbd5e1; margin:0; font-size:17px; }}
  .tl footer {{ border-top:1px solid #eadfd8; padding:26px 0 40px; color:#6b7280; font-size:14px; display:flex; justify-content:space-between; gap:20px; flex-wrap:wrap; }}
  @media (max-width: 980px) {{
    .tl .hero, .tl .two, .tl .chan {{ grid-template-columns:1fr; }} .tl .steps, .tl .limits, .tl .flow {{ grid-template-columns:1fr; }}
    .tl .mgrid {{ grid-template-columns:1fr 1fr; }}
    .tl .stats {{ grid-template-columns:1fr 1fr; }} .tl h1 {{ font-size:44px; }} .tl .links {{ display:none; }} .tl .wrap {{ padding:0 18px; }}
    .tl .final {{ flex-direction:column; align-items:flex-start; padding:30px; }}
  }}
</style>
<div class="tl">
  <div class="wrap">
    <nav>
      <div class="brand">{LOGO}<div><div class="wm"><b>Tilik</b>Care</div><div class="tg">Find. Verify. Better.</div></div></div>
      <div class="links"><a href="#how">How it works</a><a href="#evidence">Evidence</a><a href="#channels">Numbers</a><a href="#limits">Limits</a></div>
      <a class="btn" href="{DASH}" target="_self">Find a facility &rarr;</a>
    </nav>

    <div class="hero">
      <div>
        <div class="eyebrow">TILIKCARE | CARE FINDER FOR INDIA</div>
        <h1>FIND THE RIGHT CARE<br><em>near your patient</em></h1>
        <div class="lead">Tell us <b>where you are</b> and <b>what care you need</b>. We find the <b>nearest place</b> that offers it.</div>
        <div class="bar"></div>
        <div class="cta">
          <a class="btn" href="{DASH}" target="_self">Find a healthcare facility &rarr;</a>
          <a class="btn ghost" href="#how">See how it works</a>
        </div>
        <div class="loc"><img src="{PIN_ICON}" width="20" height="20" alt="">
          Piloted for Gaya District, Bihar, India</div>
      </div>
      <div class="shot" aria-label="Illustration of the TilikCare dashboard">
        <div class="top"><div class="sb"><div><b>What care</b>Dialysis</div><div><b>Where</b>Gaya, Bihar</div><span class="go">Search</span></div></div>
        <div class="body">
          <div class="cards">
            <div class="c on">#1 · 1.3 km away<b>City Hospital</b><span class="chip">Evidence 4/4</span> <span class="ok">✓ capability ✓ procedure ✓ equipment</span>
              <div style="margin-top:6px;font-style:italic;color:#374151">"Hemodialysis machine (6)"</div></div>
            <div class="c">#2 · 2.4 km away<b>Kidney and Heart Centre</b><span class="chip">Evidence 4/4</span> <span class="ok">✓ ✓ ✓</span></div>
            <div class="c">#3 · 6.1 km away<b>District Hospital</b><span class="chip o">Evidence 2/4</span> <span class="no">⚠ wording says “planned”</span></div>
          </div>
          <div class="map">
            <img class="india" src="{india_svg_paths()}" alt="">
            <span class="pp sel" style="left:64%;top:40%">1.3 km</span><span class="pp" style="left:70%;top:47%">2.4 km</span>
            <span class="pp o" style="left:58%;top:52%">6.1 km</span><span class="me" style="left:61%;top:45%"></span>
          </div>
        </div>
      </div>
    </div>

    <div class="strip"><span class="lbl">BUILT WITH</span><div class="mq"><div class="mqi">
      <span>Databricks Free Edition</span><span>Unity Catalog</span><span>Llama 3.3 70B</span><span>GTE embeddings</span>
      <span>Virtue Foundation facility data</span><span>India Post PIN directory</span><span>OpenStreetMap</span>
      <span>Databricks Free Edition</span><span>Unity Catalog</span><span>Llama 3.3 70B</span><span>GTE embeddings</span>
      <span>Virtue Foundation facility data</span><span>India Post PIN directory</span><span>OpenStreetMap</span>
    </div></div></div>
  </div>

  <section class="alt" id="how"><div class="wrap">
    <div class="eyebrow">HOW IT WORKS</div>
    <h2>How TilikCare <em>reads a request</em></h2>
    <div class="sub">From what the patient needs to the nearest place that can help, in five steps. TilikCare finds a service; it never diagnoses.</div>
    <div class="flow">
      <div class="fs"><div class="fb"></div><div class="num">1</div><h3>Understand</h3><p>Name lookup, then meaning, then AI. Hindi and Hinglish work too.</p><div class="ex">"gurde kharab hai" → Dialysis</div></div>
      <div class="fs"><div class="fb"></div><div class="num">2</div><h3>Locate</h3><p>City, PIN code, your current location, or a pin on the map.</p><div class="ex">823001 → Gaya PIN centre</div></div>
      <div class="fs"><div class="fb"></div><div class="num">3</div><h3>Match</h3><p>Facilities whose own record mentions the service, nearest first.</p><div class="ex">20 nearest candidates</div></div>
      <div class="fs"><div class="fb"></div><div class="num">4</div><h3>Verify</h3><p>AI check removes "planned" and "not offered"; quotes checked word for word.</p><div class="ex">"cure without dialysis" → removed</div></div>
      <div class="fs"><div class="fb"></div><div class="num">5</div><h3>Decide</h3><p>Evidence checklist, road distance, then call or get directions.</p><div class="ex">4 of 4 checks · ~6 min drive</div></div>
    </div>
    <div class="cta" style="margin-top:32px"><a class="btn" href="{DASH}" target="_self">Find a healthcare facility &rarr;</a></div>
  </div></section>

  <section id="evidence"><div class="wrap">
    <div class="two">
      <div>
        <div class="eyebrow">WHY THIS FACILITY</div>
        <h2>Know why each place is suggested</h2>
        <div class="sub">Before you send a patient, see what the facility says it offers: a short checklist with the exact lines from its record.</div>
        <ul class="ticks">
          <li>A checklist per facility, with the matching lines from its own record</li>
          <li>An AI check removes "planned" and "not offered" mentions, and flags unclear ones</li>
          <li>Every quote is verified word for word, with a link to the source page</li>
          <li>Same input, same answer: results are repeatable</li>
        </ul>
      </div>
      <div class="cl">
        <div class="h">Evidence checklist · City Hospital · 4 of 4 checks met</div>
        <div class="r y"><div class="i">✓</div><div><div class="t"><b>States it as a capability</b></div><div class="e">"Dialysis unit with 6 machines"</div></div></div>
        <div class="r y"><div class="i">✓</div><div><div class="t"><b>Lists a matching procedure</b></div><div class="e">Hemodialysis · CAPD</div></div></div>
        <div class="r y"><div class="i">✓</div><div><div class="t"><b>Lists matching equipment</b></div><div class="e">Hemodialysis machine (6)</div></div></div>
        <div class="r y"><div class="i">✓</div><div><div class="t"><b>Mentioned in more than one part</b></div><div class="e">capability, procedure, equipment</div></div></div>
        <div class="r n"><div class="i">✕</div><div><div class="t"><b>Recently confirmed by phone</b></div><div style="font-size:13px;color:#6b7280">Not verified: call ahead before moving the patient</div></div></div>
      </div>
    </div>
  </div></section>

  <section class="alt" id="channels"><div class="wrap">
    <div class="two">
      <div>
        <div class="eyebrow">BY THE NUMBERS</div>
        <h2>Small team, <em>real data</em></h2>
        <div class="sub">Every number here comes from the build itself, not a projection. The full method is one click away inside the dashboard.</div>
      </div>
      <div class="mgrid">
        <div class="m"><div class="n">~10k</div><div class="l">facility records in India</div></div>
        <div class="m"><div class="n">6</div><div class="l">critical services covered</div></div>
        <div class="m"><div class="n">1,908</div><div class="l">matches checked by AI</div></div>
        <div class="m"><div class="n">98%</div><div class="l">quotes found word for word</div></div>
        <div class="m"><div class="n">3/3</div><div class="l">repeat runs, identical results</div></div>
        <div class="m"><div class="n">160</div><div class="l">characters: one SMS answer</div></div>
      </div>
    </div>
  </div></section>

  <section id="limits"><div class="wrap">
    <div class="eyebrow">HONEST ABOUT LIMITS</div>
    <h2>What TilikCare does not know</h2>
    <div class="limits">
      <div class="lim"><b>Availability today</b>Records come from web text and can be out of date. Always call ahead.</div>
      <div class="lim"><b>Quality or cost</b>The score rates how well the text supports the service, not how good or affordable the care is.</div>
      <div class="lim"><b>Diagnosis</b>TilikCare maps a need to a service. It does not diagnose. In an emergency, call 108 first.</div>
    </div>
    <div class="eyebrow" style="margin-top:70px">GET STARTED</div>
    <h2>PUT TILIKCARE <em>to work</em></h2>
    <div class="chan">
      <div class="ch dark"><span class="tag">FOR NGOs AND COORDINATORS</span><h3>Web dashboard</h3>
        <p>Find the nearest facilities for a service on a map, compare them, and get directions or call in one tap.</p>
        <div class="cta"><a class="btn" href="{DASH}" target="_self">Find a healthcare facility &rarr;</a><a class="btn ghost light" href="#evidence">See the evidence</a></div></div>
      <div class="ch"><span class="tag">FOR HEALTH WORKERS</span><h3>SMS, no internet needed</h3>
        <p>Text a short message from any phone and get the two nearest options back, from the same engine as the web.</p>
        <div class="sms"><span class="in">&gt; CARE dialysis 823001</span><br>DIALYSIS 823001:<br>1) City Hosp 1km ev5/5 0631…<br>2) Kidney Ctr 2km ev5/5 0631…<br>Unverified. Call first.</div></div>
    </div>
    <footer><span>TilikCare · Find. Verify. Better.</span><span>Data: Virtue Foundation facility dataset · Built on Databricks Free Edition · Datathon 2026</span></footer>
  </div></section>
</div>
""")
