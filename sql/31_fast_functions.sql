-- Speed-up (22 Nov 2026): the web app reads stored copies of the views (30_fast_tables.sql) through these functions.
-- Results verified identical to find_care / resolve_location_v2 on 6 test searches (EXCEPT ALL both ways = 0 rows).
-- Rebuild the _tbl copies whenever the views' source data changes.

CREATE OR REPLACE FUNCTION workspace.carematch.resolve_location_fast(location_text STRING)
RETURNS TABLE (input_text STRING, location_type STRING, place_label STRING, origin_latitude DOUBLE, origin_longitude DOUBLE, origin_precision STRING, location_note STRING)
COMMENT 'Same as resolve_location_v2 but reads the stored _tbl copies instead of views. Used by the TilikCare web app.'
RETURN
WITH i AS (
  SELECT trim(coalesce(location_text, '')) AS t
),
p AS (
  SELECT t,
    CASE
      WHEN lower(t) IN ('', 'none', 'null', '-') THEN 'Empty'
      WHEN t RLIKE '^(PIN )?[0-9]{6}( .*)?$' THEN 'PIN'
      WHEN t LIKE 'City | %' THEN 'City label'
      ELSE 'Free text'
    END AS kind,
    regexp_extract(t, '([0-9]{6})', 1) AS pin,
    nullif(regexp_extract(t, '^City [|] (.+) [(]([^()]+)[)]$', 1), '') AS city_part,
    nullif(regexp_extract(t, '^City [|] (.+) [(]([^()]+)[)]$', 2), '') AS state_part
  FROM i
),
city_hit AS (
  SELECT count(*) AS n, max(c.city) AS city, max(c.state) AS state,
    max(c.origin_latitude) AS lat, max(c.origin_longitude) AS lon, max(c.origin_precision) AS method
  FROM workspace.carematch.city_origins c
  CROSS JOIN p
  WHERE (p.kind = 'City label' AND lower(c.city) = lower(p.city_part) AND lower(c.state) = lower(p.state_part))
     OR (p.kind = 'Free text' AND lower(c.city) = lower(p.t))
),
state_hit AS (
  SELECT count(*) AS n
  FROM workspace.carematch.canonical_states_tbl cs
  CROSS JOIN p
  WHERE p.kind IN ('Free text', 'City label') AND (lower(cs.state) = lower(p.t) OR lower(concat('State | ', cs.state)) = lower(p.t))
),
pin_hit AS (
  SELECT max(r.postcode) AS postcode,
    max(CASE WHEN r.approximate_latitude BETWEEN 6 AND 37.5 AND r.approximate_longitude BETWEEN 68 AND 97.5 THEN r.approximate_latitude END) AS lat,
    max(CASE WHEN r.approximate_latitude BETWEEN 6 AND 37.5 AND r.approximate_longitude BETWEEN 68 AND 97.5 THEN r.approximate_longitude END) AS lon,
    max(initcap(array_join(r.districts, '/'))) AS district,
    max(initcap(array_join(r.states, '/'))) AS pin_state,
    max(r.district_count) AS dc, max(r.state_count) AS sc
  FROM workspace.carematch.pincode_reference_tbl r
  CROSS JOIN p
  WHERE p.kind = 'PIN' AND r.postcode = p.pin
),
typed AS (
  SELECT p.t, ch.n AS city_n, ch.city, ch.state, ch.lat AS city_lat, ch.lon AS city_lon, ch.method AS city_method,
    ph.postcode, ph.lat AS pin_lat, ph.lon AS pin_lon, ph.district, ph.pin_state, ph.dc, ph.sc,
    CASE
      WHEN p.kind = 'Empty' THEN 'Empty'
      WHEN p.kind = 'PIN' AND ph.postcode IS NULL THEN 'Not recognized'
      WHEN p.kind = 'PIN' THEN 'PIN'
      WHEN ch.n = 1 THEN 'City'
      WHEN ch.n > 1 THEN 'Ambiguous'
      WHEN sh.n > 0 THEN 'State too broad'
      ELSE 'Not recognized'
    END AS location_type
  FROM p CROSS JOIN city_hit ch CROSS JOIN state_hit sh CROSS JOIN pin_hit ph
)
SELECT t AS input_text, location_type,
  CASE location_type
    WHEN 'City' THEN concat(city, ' (', state, ')')
    WHEN 'PIN' THEN concat('PIN ', postcode, ' ', district, ' (', pin_state, ')')
  END AS place_label,
  CASE location_type WHEN 'City' THEN city_lat WHEN 'PIN' THEN pin_lat END AS origin_latitude,
  CASE location_type WHEN 'City' THEN city_lon WHEN 'PIN' THEN pin_lon END AS origin_longitude,
  CASE
    WHEN location_type = 'City' THEN city_method
    WHEN location_type = 'PIN' AND pin_lat IS NOT NULL THEN 'Approximate (PIN centre)'
    ELSE 'None'
  END AS origin_precision,
  CASE location_type
    WHEN 'Empty' THEN 'Choose a city or PIN, or paste coordinates.'
    WHEN 'Not recognized' THEN 'Place not found. Choose a suggestion from the list, or paste coordinates.'
    WHEN 'Ambiguous' THEN 'This name exists in several states. Choose a "City | name (state)" option.'
    WHEN 'State too broad' THEN 'A state is too large to measure distance from. Choose a city or PIN, or paste coordinates.'
    WHEN 'City' THEN 'Distances are measured from an approximate city centre, not your exact position.'
    WHEN 'PIN' THEN CASE
      WHEN pin_lat IS NULL THEN 'This PIN has no usable coordinates. Choose a nearby city or paste coordinates.'
      WHEN dc > 1 OR sc > 1 THEN 'This PIN covers several districts. Distances are approximate.'
      ELSE 'Distances are measured from the approximate PIN centre.'
    END
  END AS location_note
FROM typed;

CREATE OR REPLACE FUNCTION workspace.carematch.find_care_fast(service_text STRING, location_text STRING, coordinates_text STRING)
RETURNS TABLE (nearest_rank INT, facility_name STRING, full_address STRING, distance_km DOUBLE, distance_label STRING, evidence_strength INT, strength_label STRING, strength_breakdown STRING, evidence_fields STRING, evidence_quote STRING, why_this STRING, caution_flags STRING, phone STRING, website STRING, source_url STRING, availability_now STRING, requested_service STRING, origin_precision STRING, latitude DOUBLE, longitude DOUBLE, facility_id STRING, source_record_id STRING)
COMMENT 'Same as find_care but reads the stored _tbl copies instead of views, and expects one of the 6 exact service names (the web app interprets free text first). Used by the TilikCare web app.'
RETURN
WITH sel AS (  -- the web app passes the exact service name it already chose, so no second interpretation step
  SELECT CASE WHEN service_text IN ('Dialysis', 'Emergency C-section', 'NICU', 'Cardiac surgery', 'MRI', 'Burn care') THEN service_text END AS service
),
loc AS (
  SELECT * FROM workspace.carematch.resolve_location_fast(location_text)
),
co AS (
  SELECT lat, lon, coalesce(lat BETWEEN 6 AND 37.5 AND lon BETWEEN 68 AND 97.5, false) AS ok
  FROM (
    SELECT
      try_cast(regexp_extract(x, '^(-?[0-9]+([.][0-9]+)?)[ ]*[,; ][ ]*(-?[0-9]+([.][0-9]+)?)$', 1) AS DOUBLE) AS lat,
      try_cast(regexp_extract(x, '^(-?[0-9]+([.][0-9]+)?)[ ]*[,; ][ ]*(-?[0-9]+([.][0-9]+)?)$', 3) AS DOUBLE) AS lon
    FROM (SELECT trim(coalesce(coordinates_text, '')) AS x)
  )
),
origin AS (
  SELECT
    CASE WHEN co.ok THEN co.lat ELSE loc.origin_latitude END AS o_lat,
    CASE WHEN co.ok THEN co.lon ELSE loc.origin_longitude END AS o_lon,
    CASE WHEN co.ok THEN 'your coordinates' ELSE concat('the approximate centre of ', loc.place_label) END AS o_label,
    CASE WHEN co.ok THEN 'Exact (entered coordinates)' ELSE loc.origin_precision END AS o_precision
  FROM co CROSS JOIN loc
),
cand AS (
  SELECT c.facility_id, c.source_record_id, c.facility_name, c.requested_service, c.latitude, c.longitude,
    CASE WHEN cv.quote_found THEN cv.quote ELSE c.evidence_quote END AS evidence_quote, c.source_url, c.availability_now,
    d.full_address, d.phone, d.website,
    st.evidence_strength, st.strength_label, st.strength_breakdown, st.evidence_fields,
    concat_ws('; ', nullif(c.evidence_flags, ''), nullif(c.identity_flags, ''), d.address_flag, CASE WHEN cv.status = 'unclear' THEN 'AI check: text does not clearly confirm this service' END) AS caution_flags,
    o.o_label, o.o_precision,
    2 * 6371 * asin(sqrt(least(1.0,
      pow(sin(radians(c.latitude - o.o_lat) / 2), 2)
      + cos(radians(o.o_lat)) * cos(radians(c.latitude)) * pow(sin(radians(c.longitude - o.o_lon) / 2), 2)))) AS dist
  FROM workspace.carematch.service_candidates_tbl c
  CROSS JOIN sel
  CROSS JOIN origin o
  LEFT JOIN workspace.carematch.facility_details_tbl d ON d.source_record_id = c.source_record_id
  LEFT JOIN workspace.carematch.candidate_strength_tbl st ON st.source_record_id = c.source_record_id AND st.requested_service = c.requested_service
  LEFT JOIN workspace.carematch.candidate_verification cv ON cv.source_record_id = c.source_record_id AND cv.requested_service = c.requested_service
  WHERE c.requested_service = sel.service
    AND coalesce(cv.status, 'offered') NOT IN ('not_offered', 'planned')
    AND o.o_lat IS NOT NULL
    AND c.latitude BETWEEN 6 AND 37.5 AND c.longitude BETWEEN 68 AND 97.5
),
ranked AS (
  SELECT *, CAST(row_number() OVER (ORDER BY dist, facility_name) AS INT) AS rk FROM cand
)
SELECT
  rk, facility_name, full_address, round(dist, 1),
  concat(format_number(dist, 1), ' km straight-line (not road distance)'),
  coalesce(evidence_strength, 0), coalesce(strength_label, 'Weak text evidence'), coalesce(strength_breakdown, ''),
  evidence_fields, evidence_quote,
  concat('#', rk, ' nearest: ', format_number(dist, 1), ' km straight-line from ', o_label, '. ',
    'Evidence ', coalesce(evidence_strength, 0), '/5 (', coalesce(strength_label, 'Weak text evidence'), '): ',
    coalesce(nullif(strength_breakdown, ''), 'no supporting field details'), '. ',
    CASE WHEN caution_flags <> '' THEN concat('Caution: ', caution_flags, '. ') ELSE '' END,
    'Text match, not verified. Call ahead to confirm.'),
  caution_flags, phone, website, source_url, availability_now, requested_service, o_precision,
  latitude, longitude, facility_id, source_record_id
FROM ranked
WHERE rk <= 20;
