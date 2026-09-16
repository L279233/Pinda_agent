-- ============================================================
-- EduAgent PostgreSQL 数据库初始化脚本
-- Docker 启动时自动执行（挂载到 /docker-entrypoint-initdb.d/）
-- ============================================================

-- 启用 UUID 自动生成扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 用户与权限表
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    username        VARCHAR(64) NOT NULL,
    email           VARCHAR(128) NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            VARCHAR(16) NOT NULL
                    CONSTRAINT users_role_check
                    CHECK (role IN ('student', 'teacher', 'admin', 'hr', 'recruiter')),
    class_id        UUID,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, email)
);
CREATE INDEX idx_users_tenant_id ON users (tenant_id);
CREATE INDEX idx_users_role ON users (role);
CREATE INDEX idx_users_class_id ON users (class_id);

-- ============================================================
-- 知识库待补充队列
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_pending_queue (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    question        TEXT NOT NULL,
    student_id      UUID REFERENCES users(id),
    confidence      FLOAT NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'resolved', 'dismissed')),
    resolved_by     UUID REFERENCES users(id),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_knowledge_pending_queue_tenant_id ON knowledge_pending_queue (tenant_id);
CREATE INDEX idx_knowledge_pending_queue_status ON knowledge_pending_queue (status);

-- ============================================================
-- 试卷批改相关表
-- ============================================================
CREATE TABLE IF NOT EXISTS exams (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    title           VARCHAR(256) NOT NULL,
    description     TEXT,
    due_date        TIMESTAMPTZ,
    created_by      UUID REFERENCES users(id),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_exams_tenant_id ON exams (tenant_id);

CREATE TABLE IF NOT EXISTS questions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    exam_id         UUID REFERENCES exams(id) ON DELETE CASCADE,
    question_no     INT NOT NULL,
    question_type   VARCHAR(16) NOT NULL
                    CHECK (question_type IN ('single_choice', 'multi_choice', 'judge', 'short_answer', 'code')),
    content         TEXT NOT NULL,
    correct_answer  TEXT,
    score           INT NOT NULL DEFAULT 10,
    knowledge_tag   VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_questions_exam_id ON questions (exam_id);
CREATE INDEX idx_questions_knowledge_tag ON questions (knowledge_tag);

CREATE TABLE IF NOT EXISTS scoring_points (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_id     UUID REFERENCES questions(id) ON DELETE CASCADE,
    point_desc      TEXT NOT NULL,
    point_score     INT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    confirmed_by    UUID REFERENCES users(id),
    confirmed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_scoring_points_question_id ON scoring_points (question_id);

CREATE TABLE IF NOT EXISTS exam_submissions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    exam_id         UUID REFERENCES exams(id),
    student_id      UUID REFERENCES users(id),
    source          VARCHAR(16) NOT NULL DEFAULT 'word'
                    CHECK (source IN ('word', 'online', 'miniapp')),
    word_minio_path VARCHAR(512),
    status          VARCHAR(16) NOT NULL DEFAULT 'submitted'
                    CHECK (status IN ('submitted', 'ai_processing', 'pending_review', 'reviewed', 'published')),
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    weak_points          JSONB,
    weak_points_summary  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exam_id, student_id)
);
CREATE INDEX idx_exam_submissions_tenant_id ON exam_submissions (tenant_id);
CREATE INDEX idx_exam_submissions_exam_id ON exam_submissions (exam_id);
CREATE INDEX idx_exam_submissions_student_id ON exam_submissions (student_id);
CREATE INDEX idx_exam_submissions_status ON exam_submissions (status);

CREATE TABLE IF NOT EXISTS exam_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    submission_id   UUID REFERENCES exam_submissions(id) ON DELETE CASCADE,
    question_id     UUID REFERENCES questions(id),
    question_type   VARCHAR(16) NOT NULL,
    knowledge_tag   VARCHAR(128),
    student_answer  TEXT,
    ai_score        INT,
    ai_feedback     TEXT,
    ai_raw_result   JSONB,
    teacher_score   INT,
    teacher_comment TEXT,
    final_score     INT,
    needs_review    BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by     UUID REFERENCES users(id),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_exam_reviews_submission_id ON exam_reviews (submission_id);
CREATE INDEX idx_exam_reviews_needs_review ON exam_reviews (needs_review);
CREATE INDEX idx_exam_reviews_knowledge_tag ON exam_reviews (knowledge_tag);

-- ============================================================
-- 简历审查相关表
-- ============================================================
CREATE TABLE IF NOT EXISTS resume_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id      UUID REFERENCES users(id),
    pdf_minio_path  VARCHAR(512) NOT NULL,
    structured_data JSONB,
    scores          JSONB,
    issues          JSONB,
    summary         JSONB,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    error_msg       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_resume_reviews_tenant_id ON resume_reviews (tenant_id);
CREATE INDEX idx_resume_reviews_student_id ON resume_reviews (student_id);
CREATE INDEX idx_resume_reviews_status ON resume_reviews (status);

-- ============================================================
-- 模拟面试相关表
-- ============================================================
CREATE TABLE IF NOT EXISTS interview_questions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    content         TEXT NOT NULL,
    difficulty      VARCHAR(8) NOT NULL DEFAULT 'medium'
                    CHECK (difficulty IN ('easy', 'medium', 'hard')),
    tags            JSONB NOT NULL DEFAULT '[]',
    target_position VARCHAR(128) NOT NULL DEFAULT 'general',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_interview_questions_tenant_id       ON interview_questions (tenant_id);
CREATE INDEX idx_interview_questions_target_position ON interview_questions (target_position);
CREATE INDEX idx_interview_questions_difficulty      ON interview_questions (difficulty);
CREATE INDEX idx_interview_questions_is_active       ON interview_questions (is_active);

CREATE TABLE IF NOT EXISTS interview_sessions (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id       UUID REFERENCES users(id),
    session_id       VARCHAR(128) NOT NULL,
    thread_id        VARCHAR(128) NOT NULL UNIQUE,
    target_position  VARCHAR(128) NOT NULL DEFAULT '',
    resume_review_id UUID REFERENCES resume_reviews(id),
    summary          TEXT,
    report           JSONB,
    overall_score    INT,
    status           VARCHAR(16) NOT NULL DEFAULT 'in_progress'
                     CHECK (status IN ('in_progress', 'finished')),
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_interview_sessions_tenant_id  ON interview_sessions (tenant_id);
CREATE INDEX idx_interview_sessions_student_id ON interview_sessions (student_id);
CREATE INDEX idx_interview_sessions_session_id ON interview_sessions (session_id);
CREATE INDEX idx_interview_sessions_status     ON interview_sessions (status);

-- ============================================================
-- 问答会话表
-- ============================================================
CREATE TABLE IF NOT EXISTS qa_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id      UUID REFERENCES users(id),
    thread_id       VARCHAR(128) NOT NULL UNIQUE,
    summary         TEXT,
    summary_version INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_qa_sessions_tenant_id ON qa_sessions (tenant_id);
CREATE INDEX idx_qa_sessions_student_id ON qa_sessions (student_id);
CREATE INDEX idx_qa_sessions_thread_id ON qa_sessions (thread_id);

-- ============================================================
-- 招聘业务：岗位与候选人申请
-- 四个 Agent 仍使用原有结果表，本模块只负责业务关联和流程状态。
-- ============================================================
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
);
CREATE INDEX idx_job_positions_tenant_status
    ON job_positions (tenant_id, status);
CREATE UNIQUE INDEX uq_job_positions_exam_id
    ON job_positions (exam_id) WHERE exam_id IS NOT NULL;

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
    CHECK (exam_deadline IS NULL OR exam_open_at IS NULL OR exam_deadline > exam_open_at),
    CHECK (
        interview_deadline IS NULL OR interview_open_at IS NULL
        OR interview_deadline > interview_open_at
    )
);
CREATE INDEX idx_recruitment_applications_candidate
    ON recruitment_applications (candidate_id, created_at DESC);
CREATE INDEX idx_recruitment_applications_position_status
    ON recruitment_applications (position_id, status);
CREATE INDEX idx_recruitment_applications_tenant_status
    ON recruitment_applications (tenant_id, status);
CREATE UNIQUE INDEX uq_recruitment_applications_resume_review
    ON recruitment_applications (resume_review_id)
    WHERE resume_review_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recruitment_applications_exam_submission
    ON recruitment_applications (exam_submission_id)
    WHERE exam_submission_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recruitment_applications_interview_session
    ON recruitment_applications (interview_session_id)
    WHERE interview_session_id IS NOT NULL;

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
);
CREATE INDEX idx_notifications_recipient
    ON notifications (tenant_id, recipient_id, created_at DESC);

CREATE TABLE IF NOT EXISTS recruitment_decision_audits (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         VARCHAR(64) NOT NULL,
    application_id    UUID NOT NULL REFERENCES recruitment_applications(id) ON DELETE CASCADE,
    actor_id          UUID NOT NULL REFERENCES users(id),
    previous_decision VARCHAR(24),
    new_decision      VARCHAR(24) NOT NULL,
    comment           TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_decision_audits_application
    ON recruitment_decision_audits (tenant_id, application_id, created_at DESC);

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
);
CREATE INDEX idx_knowledge_documents_tenant_created
    ON knowledge_documents (tenant_id, created_at DESC);

-- ============================================================
-- 自动更新 updated_at 触发器
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'users',
        'exams', 'exam_submissions', 'exam_reviews',
        'resume_reviews', 'interview_sessions',
        'interview_questions', 'qa_sessions',
        'job_positions', 'recruitment_applications',
        'knowledge_documents'
    ]
    LOOP
        EXECUTE format('
            CREATE TRIGGER trg_%s_updated_at
            BEFORE UPDATE ON %s
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        ', t, t);
    END LOOP;
END;
$$;
