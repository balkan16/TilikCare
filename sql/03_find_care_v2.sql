-- CareMatch v2, step 3: same inputs and output columns as find_care, new candidate set and ranking.
-- Ranking: within the search radius (max of 25 km or 2x the nearest match), stronger evidence first, then distance.
-- Matches outside the radius follow, nearest first. One row per facility (best record).
CREATE OR REPLACE FUNCTION workspace.carematch.find_care_v2(service_text STRING, location_text STRING, coordinates_text STRING)
RETURNS TABLE (nearest_rank INT, facility_name STRING, full_address STRING, distance_km DOUBLE, distance_label STRING,
  evidence_strength INT, strength_label STRING, strength_breakdown STRING, evidence_fields STRING, evidence_quote STRING,
  why_this STRING, caution_flags STRING, phone STRING, website STRING, source_url STRING, availability_now STRING,
  requested_service STRING, origin_precision STRING, latitude DOUBLE, longitude DOUBLE, facility_id STRING, source_record_id STRING)
COMMENT 'v2: up to 20 options for the selected service. Within the search radius (max of 25 km or 2x the nearest match) stronger text evidence ranks first, then distance; one row per facility.'
RETURN
WITH sel AS (
  SELECT max(CASE WHEN is_selected THEN service END) AS service FROM workspace.carematch.suggest_services(service_text)
),
loc AS (
  SELECT * FROM workspace.carematch.resolve_location_v2(location_text)
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
    c.evidence_quote, c.source_url, c.availability_now,
    d.full_address, d.phone, d.website,
    coalesce(st.evidence_strength, 0) AS evidence_strength,
    coalesce(st.strength_label, 'Weak text evidence') AS strength_label,
    coalesce(st.strength_breakdown, '') AS strength_breakdown,
    st.evidence_fields,
    concat_ws('; ', nullif(c.evidence_flags, ''), nullif(c.identity_flags, ''), d.address_flag) AS caution_flags,
    o.o_label, o.o_precision,
    2 * 6371 * asin(sqrt(least(1.0,
      pow(sin(radians(c.latitude - o.o_lat) / 2), 2)
      + cos(radians(o.o_lat)) * cos(radians(c.latitude)) * pow(sin(radians(c.longitude - o.o_lon) / 2), 2)))) AS dist
  FROM workspace.carematch.service_candidates_v2 c
  CROSS JOIN sel
  CROSS JOIN origin o
  LEFT JOIN workspace.carematch.facility_details d ON d.source_record_id = c.source_record_id
  LEFT JOIN workspace.carematch.candidate_strength_v2 st ON st.source_record_id = c.source_record_id AND st.requested_service = c.requested_service
  WHERE c.requested_service = sel.service
    AND o.o_lat IS NOT NULL
    AND c.latitude BETWEEN 6 AND 37.5 AND c.longitude BETWEEN 68 AND 97.5
),
one_per_facility AS (
  SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY facility_id ORDER BY evidence_strength DESC, dist) AS dup_rk FROM cand
  ) WHERE dup_rk = 1
),
radius AS (
  SELECT *, greatest(25.0, 2 * min(dist) OVER ()) AS reach_km, min(dist) OVER () AS nearest_km FROM one_per_facility
),
tiered AS (
  SELECT *,
    dist <= reach_km AS in_reach,
    CASE WHEN evidence_strength >= 4 THEN 1 WHEN evidence_strength >= 2 THEN 2 ELSE 3 END AS tier
  FROM radius
),
ranked AS (
  SELECT *, CAST(row_number() OVER (
      ORDER BY CASE WHEN in_reach THEN 0 ELSE 1 END,
               CASE WHEN in_reach THEN tier ELSE 0 END,
               dist, facility_name) AS INT) AS rk
  FROM tiered
)
SELECT
  rk, facility_name, full_address, round(dist, 1),
  concat(format_number(dist, 1), ' km straight-line (not road distance)'),
  evidence_strength, strength_label, strength_breakdown, evidence_fields, evidence_quote,
  concat('#', rk, ': ', format_number(dist, 1), ' km straight-line from ', o_label, '. ',
    'Evidence ', evidence_strength, '/5 (', strength_label, '): ',
    coalesce(nullif(strength_breakdown, ''), 'no supporting field details'), '. ',
    CASE WHEN in_reach
         THEN concat('Within the ', format_number(reach_km, 0), ' km search radius, so ranked by evidence first, then distance. ')
         ELSE concat('Outside the ', format_number(reach_km, 0), ' km search radius, ranked by distance only. ') END,
    CASE WHEN nearest_km > 50 THEN 'Note: the nearest match is more than 50 km away. ' ELSE '' END,
    CASE WHEN caution_flags <> '' THEN concat('Caution: ', caution_flags, '. ') ELSE '' END,
    'Text match, not verified. Call ahead to confirm.'),
  caution_flags, phone, website, source_url, availability_now, requested_service, o_precision,
  latitude, longitude, facility_id, source_record_id
FROM ranked
WHERE rk <= 20;
