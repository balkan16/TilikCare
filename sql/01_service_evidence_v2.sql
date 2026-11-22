-- CareMatch v2, step 1: keyword evidence with wider synonyms and a local negation check.
-- Built next to v1 (service_evidence); nothing in v1 is changed.
CREATE OR REPLACE TABLE workspace.carematch.service_evidence_v2
COMMENT 'v2 keyword evidence. One row per (record, service, field): the best matching list item or sentence. Wider synonyms than v1; negation/planned is checked only next to the service term. Text match, not verified.'
AS
WITH kw AS (
  SELECT * FROM VALUES
    ('Dialysis',            '(dialysis|haemodialysis|hemodialysis|hemo-dialysis|\\bcapd\\b|\\bcrrt\\b|renal replacement therapy)'),
    ('Emergency C-section', '(c-section|c section|caesarean|cesarean|caeserean|cesarian|caesarian|\\blscs\\b)'),
    ('NICU',                '(\\bnicu\\b|neonatal intensive|neonatal icu|newborn intensive|neonatal critical care|sick newborn care unit|\\bsncu\\b)'),
    ('Cardiac surgery',     '(cardiac surg|heart surg|cardiothoracic|cardio-thoracic|cardio thoracic|\\bctvs\\b|bypass surg|\\bcabg\\b|open heart|valve replacement|valve repair|heart transplant)'),
    ('MRI',                 '(\\bmri\\b|magnetic resonance|\\bmr imaging\\b)'),
    ('Burn care',           '(\\bburns? (unit|ward|care|centre|center|icu|management|treatment|surgery|reconstruction|dressing)|\\bburns? (and|&) plastic|(plastic|reconstructive)[^.;]{0,20}\\bburns?\\b|\\bburns? patients|\\bburn injur)')
  AS t(service, pattern)
),
fr AS (
  SELECT *, length(coalesce(description, '')) AS desc_len FROM workspace.carematch.facility_records
),
items AS (
  SELECT source_record_id, 'procedure'   AS field, 1 AS fprio, explode(coalesce(from_json(procedure,   'array<string>'), array(procedure)))   AS item FROM fr WHERE procedure   IS NOT NULL
  UNION ALL
  SELECT source_record_id, 'capability'  AS field, 2 AS fprio, explode(coalesce(from_json(capability,  'array<string>'), array(capability)))  AS item FROM fr WHERE capability  IS NOT NULL
  UNION ALL
  SELECT source_record_id, 'equipment'   AS field, 3 AS fprio, explode(coalesce(from_json(equipment,   'array<string>'), array(equipment)))   AS item FROM fr WHERE equipment   IS NOT NULL
  UNION ALL
  SELECT source_record_id, 'specialties' AS field, 4 AS fprio, explode(coalesce(from_json(specialties, 'array<string>'), array(specialties))) AS item FROM fr WHERE specialties IS NOT NULL
  UNION ALL
  SELECT source_record_id, 'description' AS field, 5 AS fprio, explode(split(description, '(?<=[.!?;])\\s+')) AS item FROM fr WHERE description IS NOT NULL
),
m AS (
  SELECT i.source_record_id, i.field, i.fprio, trim(i.item) AS item, k.service,
    (lower(i.item) RLIKE concat('(\\bno\\b|\\bnot\\b|\\bwithout\\b|\\bnor\\b|lack of|absence of|non-availability|planned|upcoming|proposed|to be started|coming soon|under construction)[^.;]{0,30}', k.pattern)
     OR lower(i.item) RLIKE concat(k.pattern, '[^.;]{0,40}(not available|unavailable|not provided|not offered|not performed|planned|upcoming|coming soon|proposed|under construction|referred to|refer to)')) AS negated
  FROM items i JOIN kw k ON lower(i.item) RLIKE k.pattern
),
best_per_field AS (
  SELECT * FROM (
    SELECT m.*, row_number() OVER (PARTITION BY source_record_id, service, field ORDER BY negated, length(item)) AS rn FROM m
  ) WHERE rn = 1
),
ordered AS (
  SELECT b.*, row_number() OVER (PARTITION BY source_record_id, service ORDER BY negated, fprio) AS evidence_display_order
  FROM best_per_field b
)
SELECT
  r.facility_id, o.source_record_id, r.facility_name, r.organization_type, r.state, r.city, r.postcode,
  r.latitude, r.longitude,
  o.service AS requested_service,
  'Text-match candidate (v2) - not verified' AS match_status,
  left(o.item, 400) AS evidence_quote,
  o.item AS original_evidence_text,
  o.field AS evidence_field,
  o.negated,
  r.source_url, r.source_urls_raw, r.source_update_text,
  CAST(NULL AS DOUBLE) AS distance_km,
  r.location_status, r.identity_flags,
  concat_ws('; ',
    CASE WHEN o.negated THEN 'Possible negation or planned service next to the term' END,
    CASE WHEN o.field = 'description' AND r.desc_len > 2000 THEN 'Long text may have ambiguous facility attribution' END) AS evidence_flags,
  concat('Included because a v2 keyword for ', o.service, ' appears in the ', o.field, ' field.') AS inclusion_reason,
  'Ranked in find_care_v2: evidence tier first within the search radius, then distance.' AS ranking_reason,
  'Unknown unless explicitly supported by current evidence' AS availability_now,
  current_timestamp() AS derived_at,
  o.evidence_display_order
FROM ordered o
JOIN fr r ON r.source_record_id = o.source_record_id;
