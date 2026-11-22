-- Service classifier, step 1: service profiles (text + embedding) and a labelled test set.
-- Profiles describe each supported service in plain words, incl. typical situations, so free text can be matched by meaning.
CREATE OR REPLACE TABLE workspace.carematch.service_profiles
COMMENT 'Plain-language profile per supported service + gte-large-en embedding. Used by classify_service (embedding step). Profiles written by the team; not medical advice.'
AS
WITH p AS (
  SELECT * FROM VALUES
    ('Dialysis', 'Dialysis for kidney failure. Haemodialysis or peritoneal dialysis. Patient with kidney disease or renal failure, very little or no urine, swelling of legs and face, high creatinine, needs a kidney machine.'),
    ('Emergency C-section', 'Emergency caesarean section (C-section) for a pregnant woman in labour. Obstructed or very long labour, baby not coming out, baby in wrong position, heavy bleeding in pregnancy, fits in pregnancy, urgent operation to deliver the baby.'),
    ('NICU', 'Neonatal intensive care unit (NICU) for a sick or premature newborn baby. Baby born too early or very small, low birth weight, newborn breathing difficulty, newborn not feeding, newborn yellow jaundice, newborn infection, incubator.'),
    ('Cardiac surgery', 'Heart care and cardiac surgery. Heart attack, chest pain spreading to the arm, blocked heart arteries, angioplasty, bypass surgery, heart valve disease, hole in the heart, open heart operation.'),
    ('MRI', 'MRI scan (magnetic resonance imaging) for detailed pictures of the brain, spine, joints and soft tissue. Doctor asked for an MRI, brain scan, spine scan, tumour scan.'),
    ('Burn care', 'Burn care for burn injuries. Burns from fire, hot water, hot oil, chemicals or electricity, skin burnt or peeling after burning, burn wound dressing, burn unit, skin graft.')
  AS t(service, profile)
)
SELECT service, profile, CAST(ai_query('databricks-gte-large-en', profile) AS ARRAY<DOUBLE>) AS emb FROM p;

CREATE OR REPLACE TABLE workspace.carematch.classifier_testset
COMMENT 'Hand-written test phrases with the expected service (none = not one of the 6). For calibrating classify_service thresholds.'
AS
SELECT phrase, expected, CAST(ai_query('databricks-gte-large-en', phrase) AS ARRAY<DOUBLE>) AS emb FROM VALUES
  ('dialysis', 'Dialysis'),
  ('kidney failure need dialysis', 'Dialysis'),
  ('patient urine stopped and legs swollen', 'Dialysis'),
  ('kidney kharab hai machine lagana hai', 'Dialysis'),
  ('pregnant woman labour pain since 20 hours baby not coming', 'Emergency C-section'),
  ('heavy bleeding during delivery need operation', 'Emergency C-section'),
  ('c sec', 'Emergency C-section'),
  ('newborn baby born early very small breathing problem', 'NICU'),
  ('2 day old baby yellow and not feeding', 'NICU'),
  ('chest pain sweating left arm pain', 'Cardiac surgery'),
  ('heart valve operation', 'Cardiac surgery'),
  ('dil me dard aur pasina', 'Cardiac surgery'),
  ('doctor asked for brain mri', 'MRI'),
  ('need scan of spine', 'MRI'),
  ('hot oil fell on child arm skin peeling', 'Burn care'),
  ('fire accident burns on body', 'Burn care'),
  ('stomachache', 'none'),
  ('fever and cough for 3 days', 'none'),
  ('broken leg after bike accident', 'none'),
  ('tooth pain', 'none'),
  ('eye checkup', 'none'),
  ('snake bite', 'none')
AS t(phrase, expected);
