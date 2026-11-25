ALTER TABLE interview_exams ADD COLUMN exam_lane smallint CHECK (exam_lane BETWEEN 1 AND 4);
