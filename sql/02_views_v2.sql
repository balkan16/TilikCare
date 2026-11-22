-- CareMatch v2, step 2: one candidate row per (record, service) + evidence score.
CREATE OR REPLACE VIEW workspace.carematch.service_candidates_v2
COMMENT 'v2 candidates: best evidence row per (record, service). Text match, not verified.'
AS SELECT * EXCEPT (evidence_display_order) FROM workspace.carematch.service_evidence_v2 WHERE evidence_display_order = 1;

CREATE OR REPLACE VIEW workspace.carematch.candidate_strength_v2
COMMENT 'v2 rule-based evidence strength 0-5. Same rules as v1, but only non-negated mentions count, and the -2 negation penalty applies only when every mention is negated. Rates the TEXT, not hospital quality.'
AS
WITH ev AS (
  SELECT source_record_id, requested_service,
    max(CASE WHEN evidence_field = 'capability' AND NOT negated THEN 1 ELSE 0 END) AS in_capability,
    max(CASE WHEN evidence_field = 'procedure'  AND NOT negated THEN 1 ELSE 0 END) AS in_procedure,
    max(CASE WHEN evidence_field = 'equipment'  AND NOT negated THEN 1 ELSE 0 END) AS in_equipment,
    count(DISTINCT CASE WHEN NOT negated THEN evidence_field END) AS n_fields,
    array_join(array_sort(collect_set(CASE WHEN NOT negated THEN evidence_field END)), ', ') AS evidence_fields,
    min(CASE WHEN negated THEN 1 ELSE 0 END) AS all_negated
  FROM workspace.carematch.service_evidence_v2
  GROUP BY source_record_id, requested_service
),
base AS (
  SELECT c.source_record_id, c.requested_service,
    ev.in_capability, ev.in_procedure, ev.in_equipment, ev.n_fields, ev.evidence_fields,
    CASE WHEN coalesce(c.source_url, '') NOT IN ('', 'null', '[]') THEN 1 ELSE 0 END AS has_source,
    ev.all_negated AS f_neg,
    CASE WHEN lower(coalesce(c.evidence_flags, '')) LIKE '%ambiguous%' THEN 1 ELSE 0 END AS f_amb,
    CASE WHEN coalesce(c.identity_flags, '') <> '' THEN 1 ELSE 0 END AS f_id,
    CASE WHEN coalesce(c.location_status, '') LIKE 'Review%' THEN 1 ELSE 0 END AS f_loc
  FROM workspace.carematch.service_candidates_v2 c
  JOIN ev ON ev.source_record_id = c.source_record_id AND ev.requested_service = c.requested_service
),
scored AS (
  SELECT *,
    CAST(greatest(0, least(5,
      in_capability + in_procedure + in_equipment
      + CASE WHEN n_fields >= 2 THEN 1 ELSE 0 END + has_source
      - 2 * f_neg - f_amb - f_id - f_loc)) AS INT) AS evidence_strength
  FROM base
)
SELECT source_record_id, requested_service, evidence_strength, evidence_fields,
  CASE WHEN evidence_strength >= 4 THEN 'Stronger text evidence'
       WHEN evidence_strength >= 2 THEN 'Moderate text evidence'
       ELSE 'Weak text evidence' END AS strength_label,
  concat_ws(', ',
    CASE WHEN in_capability = 1 THEN '+1 listed under capability' END,
    CASE WHEN in_procedure = 1 THEN '+1 listed under procedure' END,
    CASE WHEN in_equipment = 1 THEN '+1 listed under equipment' END,
    CASE WHEN n_fields >= 2 THEN '+1 mentioned in 2 or more fields' END,
    CASE WHEN has_source = 1 THEN '+1 has a source link' END,
    CASE WHEN f_neg = 1 THEN '-2 every mention is negated or planned' END,
    CASE WHEN f_amb = 1 THEN '-1 long text may describe another facility' END,
    CASE WHEN f_id = 1 THEN '-1 duplicate or identity warning' END,
    CASE WHEN f_loc = 1 THEN '-1 location needs review' END
  ) AS strength_breakdown
FROM scored;
