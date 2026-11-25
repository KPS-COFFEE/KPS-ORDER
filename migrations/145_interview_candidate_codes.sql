ALTER TABLE interview_rooms ADD COLUMN exam_number bigint;
WITH numbered AS (
 SELECT id, row_number() OVER (PARTITION BY owner_id ORDER BY id) AS n FROM interview_rooms
)
UPDATE interview_rooms r SET exam_number=n.n FROM numbered n WHERE n.id=r.id;
ALTER TABLE interview_rooms ALTER COLUMN exam_number SET NOT NULL;
ALTER TABLE interview_rooms ADD UNIQUE(owner_id, exam_number);
ALTER TABLE interview_panels ADD COLUMN exam_code_prefix text;
