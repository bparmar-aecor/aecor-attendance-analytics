-- =============================================================
-- schema.sql
-- Dashboard-only tables for the Aecor Attendance Analytics
-- system. Safe to run on an existing Supabase project — uses
-- IF NOT EXISTS everywhere.
--
-- Tables created/synced by the eSSL sync script (do NOT recreate):
--   employees, departments, shifts, attendance_logs,
--   device_logs, sync_log
-- =============================================================

-- -------------------------------------------------------------
-- 1. Employee categories  (BRD §4)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS employee_categories (
    id              BIGSERIAL PRIMARY KEY,
    employee_id     BIGINT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('normal', 'custom', 'excluded')),
    effective_from  DATE NOT NULL DEFAULT CURRENT_DATE,
    notes           TEXT,                       -- kept for back-compat; no longer written
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, effective_from)
);

-- Migrate any existing uppercase rows from earlier versions
DO $$
BEGIN
    -- Only run if the constraint allows it (newly created table has the lowercase check)
    -- This is a safety net for projects where the table was created with the old uppercase check
    BEGIN
        ALTER TABLE employee_categories DROP CONSTRAINT IF EXISTS employee_categories_category_check;
        UPDATE employee_categories SET category = LOWER(category) WHERE category <> LOWER(category);
        ALTER TABLE employee_categories ADD CONSTRAINT employee_categories_category_check
            CHECK (category IN ('normal', 'custom', 'excluded'));
    EXCEPTION WHEN OTHERS THEN
        -- Ignore — table is already in the right state
        NULL;
    END;
END $$;
CREATE INDEX IF NOT EXISTS idx_emp_cat_employee
    ON employee_categories (employee_id, effective_from DESC);


-- -------------------------------------------------------------
-- 2. Leave records  (BRD §10)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS employee_leaves (
    id           BIGSERIAL PRIMARY KEY,
    employee_id  BIGINT NOT NULL,
    leave_date   DATE NOT NULL,
    leave_type   TEXT NOT NULL,
    reason       TEXT,
    marked_by    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, leave_date)
);
CREATE INDEX IF NOT EXISTS idx_leaves_date
    ON employee_leaves (leave_date);
CREATE INDEX IF NOT EXISTS idx_leaves_employee
    ON employee_leaves (employee_id, leave_date);


-- -------------------------------------------------------------
-- 3. Punch regularisations  (BRD §9)
-- Action types: 'add', 'edit', 'delete'
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS punch_regularizations (
    id              BIGSERIAL PRIMARY KEY,
    employee_id     BIGINT NOT NULL,
    punch_date      DATE NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('add', 'edit', 'delete')),
    original_time   TIMESTAMPTZ,
    corrected_time  TIMESTAMPTZ,
    reason          TEXT NOT NULL,
    approved_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_employee_date
    ON punch_regularizations (employee_id, punch_date);


-- -------------------------------------------------------------
-- 4. Helper view: latest active category per employee
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW v_employee_current_category AS
SELECT DISTINCT ON (employee_id)
    employee_id,
    category,
    effective_from
FROM employee_categories
WHERE effective_from <= CURRENT_DATE
ORDER BY employee_id, effective_from DESC;
