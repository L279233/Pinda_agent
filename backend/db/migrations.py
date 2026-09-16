# backend/db/migrations.py
from sqlalchemy import text
from backend.dependencies import AsyncSessionLocal
from backend.core.logger import get_logger

logger = get_logger(__name__)

# 所有补丁，按时间顺序追加。SQL 必须幂等（带 IF NOT EXISTS）
_MIGRATIONS: list[tuple[str, str]] = [
    (
        "users.recruitment_roles",
        """
        DO $$
        BEGIN
            ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
            ALTER TABLE users ADD CONSTRAINT users_role_check
                CHECK (role IN ('student', 'teacher', 'admin', 'hr', 'recruiter'));
        END
        $$
        """,
    ),
    (
        "exam_submissions.weak_points",
        "ALTER TABLE exam_submissions ADD COLUMN IF NOT EXISTS weak_points JSONB",
    ),
    (
        "exam_reviews.knowledge_tag",
        "ALTER TABLE exam_reviews ADD COLUMN IF NOT EXISTS knowledge_tag VARCHAR(128)",
    ),
    (
        "idx_exam_submissions_student_created",
        "CREATE INDEX IF NOT EXISTS idx_exam_submissions_student_created "
        "ON exam_submissions (student_id, created_at DESC)",
    ),
    (
        "job_positions.table",
        """
        CREATE TABLE IF NOT EXISTS job_positions (
            id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id           VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
            code                VARCHAR(64) NOT NULL,
            title               VARCHAR(128) NOT NULL,
            department          VARCHAR(128),
            jd_text             TEXT NOT NULL,
            requirements        JSONB NOT NULL DEFAULT '{}'::jsonb,
            evaluation_weights  JSONB NOT NULL DEFAULT
                                '{"resume": 0.3, "exam": 0.3, "interview": 0.4}'::jsonb,
            resume_pass_score   NUMERIC(5,2) NOT NULL DEFAULT 70
                                CHECK (resume_pass_score BETWEEN 0 AND 100),
            exam_window_hours   INT NOT NULL DEFAULT 72
                                CHECK (exam_window_hours > 0),
            interview_window_hours INT NOT NULL DEFAULT 72
                                CHECK (interview_window_hours > 0),
            exam_id             UUID REFERENCES exams(id),
            status              VARCHAR(16) NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft', 'open', 'closed')),
            apply_deadline      TIMESTAMPTZ,
            created_by          UUID REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, code),
            CONSTRAINT ck_job_positions_open_exam
                CHECK (status <> 'open' OR exam_id IS NOT NULL)
        )
        """,
    ),
    (
        "idx_job_positions_tenant_status",
        "CREATE INDEX IF NOT EXISTS idx_job_positions_tenant_status "
        "ON job_positions (tenant_id, status)",
    ),
    (
        "job_positions.exam_window_hours",
        "ALTER TABLE job_positions ADD COLUMN IF NOT EXISTS "
        "exam_window_hours INT NOT NULL DEFAULT 72 CHECK (exam_window_hours > 0)",
    ),
    (
        "job_positions.interview_window_hours",
        "ALTER TABLE job_positions ADD COLUMN IF NOT EXISTS "
        "interview_window_hours INT NOT NULL DEFAULT 72 "
        "CHECK (interview_window_hours > 0)",
    ),
    (
        "job_positions.open_requires_exam",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_job_positions_open_exam'
                  AND conrelid = 'job_positions'::regclass
            ) THEN
                ALTER TABLE job_positions
                ADD CONSTRAINT ck_job_positions_open_exam
                CHECK (status <> 'open' OR exam_id IS NOT NULL);
            END IF;
        END
        $$
        """,
    ),
    (
        "uq_job_positions_exam_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_positions_exam_id "
        "ON job_positions (exam_id) WHERE exam_id IS NOT NULL",
    ),
    (
        "recruitment_applications.table",
        """
        CREATE TABLE IF NOT EXISTS recruitment_applications (
            id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id             VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
            position_id           UUID NOT NULL REFERENCES job_positions(id),
            candidate_id          UUID NOT NULL REFERENCES users(id),
            resume_review_id      UUID REFERENCES resume_reviews(id),
            exam_submission_id    UUID REFERENCES exam_submissions(id),
            interview_session_id  UUID REFERENCES interview_sessions(id),
            status                VARCHAR(24) NOT NULL DEFAULT 'draft'
                                  CHECK (status IN (
                                      'draft', 'resume_processing', 'screening',
                                      'completed', 'rejected', 'withdrawn'
                                  )),
            resume_decision       VARCHAR(16) NOT NULL DEFAULT 'pending'
                                  CHECK (resume_decision IN (
                                      'pending', 'passed', 'rejected', 'manual_review'
                                  )),
            exam_status           VARCHAR(24) NOT NULL DEFAULT 'locked'
                                  CHECK (exam_status IN (
                                      'locked', 'available', 'in_progress',
                                      'pending_review', 'completed', 'expired'
                                  )),
            interview_status      VARCHAR(24) NOT NULL DEFAULT 'locked'
                                  CHECK (interview_status IN (
                                      'locked', 'available', 'in_progress',
                                      'completed', 'expired'
                                  )),
            exam_open_at          TIMESTAMPTZ,
            exam_deadline         TIMESTAMPTZ,
            interview_open_at     TIMESTAMPTZ,
            interview_deadline    TIMESTAMPTZ,
            final_decision        VARCHAR(24)
                                  CHECK (final_decision IS NULL OR final_decision IN (
                                      'pending', 'advance', 'rejected', 'hired', 'withdrawn'
                                  )),
            decided_by            UUID REFERENCES users(id),
            decided_at            TIMESTAMPTZ,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (position_id, candidate_id),
            CHECK (
                exam_deadline IS NULL OR exam_open_at IS NULL
                OR exam_deadline > exam_open_at
            ),
            CHECK (
                interview_deadline IS NULL OR interview_open_at IS NULL
                OR interview_deadline > interview_open_at
            )
        )
        """,
    ),
    (
        "idx_recruitment_applications_candidate",
        "CREATE INDEX IF NOT EXISTS idx_recruitment_applications_candidate "
        "ON recruitment_applications (candidate_id, created_at DESC)",
    ),
    (
        "idx_recruitment_applications_position_status",
        "CREATE INDEX IF NOT EXISTS idx_recruitment_applications_position_status "
        "ON recruitment_applications (position_id, status)",
    ),
    (
        "idx_recruitment_applications_tenant_status",
        "CREATE INDEX IF NOT EXISTS idx_recruitment_applications_tenant_status "
        "ON recruitment_applications (tenant_id, status)",
    ),
    (
        "uq_recruitment_applications_resume_review",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_recruitment_applications_resume_review "
        "ON recruitment_applications (resume_review_id) "
        "WHERE resume_review_id IS NOT NULL",
    ),
    (
        "uq_recruitment_applications_exam_submission",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_recruitment_applications_exam_submission "
        "ON recruitment_applications (exam_submission_id) "
        "WHERE exam_submission_id IS NOT NULL",
    ),
    (
        "uq_recruitment_applications_interview_session",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_recruitment_applications_interview_session "
        "ON recruitment_applications (interview_session_id) "
        "WHERE interview_session_id IS NOT NULL",
    ),
    (
        "job_positions.updated_at_trigger",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_job_positions_updated_at'
                  AND tgrelid = 'job_positions'::regclass
            ) THEN
                CREATE TRIGGER trg_job_positions_updated_at
                BEFORE UPDATE ON job_positions
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            END IF;
        END
        $$
        """,
    ),
    (
        "recruitment_applications.updated_at_trigger",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_recruitment_applications_updated_at'
                  AND tgrelid = 'recruitment_applications'::regclass
            ) THEN
                CREATE TRIGGER trg_recruitment_applications_updated_at
                BEFORE UPDATE ON recruitment_applications
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            END IF;
        END
        $$
        """,
    ),
    (
        "notifications.table",
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id       VARCHAR(64) NOT NULL,
            recipient_id    UUID NOT NULL REFERENCES users(id),
            application_id  UUID REFERENCES recruitment_applications(id) ON DELETE CASCADE,
            channel         VARCHAR(16) NOT NULL DEFAULT 'in_app'
                            CHECK (channel IN ('in_app', 'email', 'sms')),
            reminder_type   VARCHAR(32) NOT NULL,
            title           VARCHAR(128) NOT NULL,
            content         TEXT NOT NULL,
            deadline        TIMESTAMPTZ,
            idempotency_key VARCHAR(256) NOT NULL UNIQUE,
            sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            read_at         TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_notifications_recipient",
        "CREATE INDEX IF NOT EXISTS idx_notifications_recipient "
        "ON notifications (tenant_id, recipient_id, created_at DESC)",
    ),
    (
        "recruitment_decision_audits.table",
        """
        CREATE TABLE IF NOT EXISTS recruitment_decision_audits (
            id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id         VARCHAR(64) NOT NULL,
            application_id    UUID NOT NULL REFERENCES recruitment_applications(id) ON DELETE CASCADE,
            actor_id          UUID NOT NULL REFERENCES users(id),
            previous_decision VARCHAR(24),
            new_decision      VARCHAR(24) NOT NULL,
            comment           TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_decision_audits_application",
        "CREATE INDEX IF NOT EXISTS idx_decision_audits_application "
        "ON recruitment_decision_audits (tenant_id, application_id, created_at DESC)",
    ),
    (
        "knowledge_documents.table",
        """
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id       VARCHAR(64) NOT NULL,
            position_id     UUID REFERENCES job_positions(id) ON DELETE SET NULL,
            document_type   VARCHAR(32) NOT NULL
                            CHECK (document_type IN (
                                'position_jd', 'policy', 'company', 'faq', 'question_bank'
                            )),
            filename        VARCHAR(255) NOT NULL,
            status          VARCHAR(16) NOT NULL DEFAULT 'processing'
                            CHECK (status IN ('processing', 'ready', 'failed')),
            use_context     BOOLEAN NOT NULL DEFAULT FALSE,
            chunk_count     INT,
            error_msg       TEXT,
            created_by      UUID NOT NULL REFERENCES users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx_knowledge_documents_tenant_created",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_tenant_created "
        "ON knowledge_documents (tenant_id, created_at DESC)",
    ),
    (
        "knowledge_documents.updated_at_trigger",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_knowledge_documents_updated_at'
                  AND tgrelid = 'knowledge_documents'::regclass
            ) THEN
                CREATE TRIGGER trg_knowledge_documents_updated_at
                BEFORE UPDATE ON knowledge_documents
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            END IF;
        END
        $$
        """,
    ),
    # 每次给 init_db.sql 加字段或对象，都在这里同步追加幂等迁移。
]


async def run_migrations() -> None:
    """应用启动时执行所有 Schema 补丁；任一失败即终止，避免带残缺 Schema 启动。"""
    async with AsyncSessionLocal() as session:
        for desc, sql in _MIGRATIONS:
            try:
                await session.execute(text(sql))
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error("db.migration_failed", migration=desc, error=str(e))
                raise RuntimeError(f"数据库迁移失败：{desc}") from e
    logger.info("db.migrations_done", count=len(_MIGRATIONS))
