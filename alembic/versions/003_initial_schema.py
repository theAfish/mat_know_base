"""Initial MKB schema reconstructed from the supported revision-003 database.

Revision ID: 003
Revises: None
"""

from alembic import op

revision = "003"
down_revision = None
branch_labels = None
depends_on = None


BASELINE_SQL = r"""
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE TYPE feedback_status AS ENUM ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED','DEV_ISSUE');
CREATE TYPE frame_status AS ENUM ('PENDING','IN_PROGRESS','COMPLETED','FAILED');
CREATE TYPE processing_status AS ENUM ('PENDING','STORED','PROCESSED','FAILED');
CREATE TYPE processing_type AS ENUM ('MARKDOWN','DATAFRAME','IMAGE','UNPROCESSABLE');
CREATE TYPE projection_status AS ENUM
  ('PENDING','IN_PROGRESS','COMPLETED','FAILED','NEEDS_FEEDBACK','REVIEWED');

CREATE TABLE assets (
  asset_id uuid PRIMARY KEY, sha256 varchar(64) NOT NULL,
  filename text NOT NULL, mime_type varchar(255) NOT NULL, size_bytes integer NOT NULL,
  s3_bucket varchar(63) NOT NULL, s3_key text NOT NULL,
  status processing_status NOT NULL, metadata jsonb, embedding vector(1536),
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);
CREATE UNIQUE INDEX ix_assets_sha256 ON assets (sha256);

CREATE TABLE extraction_passes (
  pass_id uuid PRIMARY KEY, frame_id uuid NOT NULL, pass_number integer NOT NULL,
  pass_type varchar(50) NOT NULL, content_snapshot jsonb, changes_made jsonb,
  agent_notes text, created_at timestamptz DEFAULT now() NOT NULL
);
CREATE INDEX ix_extraction_pass_frame_id ON extraction_passes (frame_id);

CREATE TABLE feedbacks (
  feedback_id uuid PRIMARY KEY, source_projection_id uuid, source_agent varchar(100) NOT NULL,
  target_frame_id uuid NOT NULL, target_project_id uuid NOT NULL,
  category varchar(50) NOT NULL, field_path text, question text NOT NULL, context text,
  status feedback_status NOT NULL, resolution_notes text, resolved_by varchar(100),
  resolved_at timestamptz, created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);
CREATE INDEX ix_feedback_status ON feedbacks (status);
CREATE INDEX ix_feedback_target_frame ON feedbacks (target_frame_id);
CREATE INDEX ix_feedback_target_project ON feedbacks (target_project_id);

CREATE TABLE graph_element_reviews (
  review_id uuid PRIMARY KEY, space_id uuid NOT NULL, element_type varchar(20) NOT NULL,
  element_key text NOT NULL, times_examined integer NOT NULL, times_modified integer NOT NULL,
  last_examined_at timestamptz, last_modified_at timestamptz,
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT uq_graph_element_review UNIQUE (space_id, element_type, element_key)
);
CREATE INDEX ix_graph_element_review_space ON graph_element_reviews (space_id, element_type);

CREATE TABLE knowledge_frames (
  frame_id uuid PRIMARY KEY, project_id uuid NOT NULL UNIQUE, status frame_status NOT NULL,
  content jsonb, extraction_summary text, times_checked integer NOT NULL,
  extraction_version integer NOT NULL, extracted_at timestamptz, source_metadata jsonb,
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);
CREATE INDEX ix_frame_project_id ON knowledge_frames (project_id);

CREATE TABLE processed_assets (
  processed_asset_id uuid PRIMARY KEY, asset_id uuid NOT NULL,
  processing_type processing_type NOT NULL, output_format varchar(50) NOT NULL,
  s3_bucket varchar(63) NOT NULL, s3_key text NOT NULL, sha256 varchar(64) NOT NULL,
  size_bytes integer NOT NULL, conversion_metadata jsonb, raw_asset_hash varchar(64) NOT NULL,
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);
CREATE INDEX ix_processed_asset_id ON processed_assets (asset_id);
CREATE INDEX ix_processed_sha256 ON processed_assets (sha256);

CREATE TABLE processing_logs (
  log_id uuid PRIMARY KEY, asset_id uuid NOT NULL, processing_type processing_type NOT NULL,
  status varchar(50) NOT NULL, processed_asset_id uuid, error_message text, details jsonb,
  created_at timestamptz DEFAULT now() NOT NULL
);

CREATE TABLE project_assets (
  project_id uuid NOT NULL, asset_id uuid NOT NULL, PRIMARY KEY (project_id, asset_id)
);

CREATE TABLE projections (
  projection_id uuid PRIMARY KEY, space_id uuid NOT NULL, frame_id uuid NOT NULL,
  status projection_status NOT NULL, data jsonb, validation_result jsonb, agent_notes text,
  extracted_at timestamptz, space_version integer NOT NULL,
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);
CREATE INDEX ix_projection_space_frame ON projections (space_id, frame_id);

CREATE TABLE research_projects (
  project_id uuid PRIMARY KEY, label text, source_path text UNIQUE, file_count integer NOT NULL,
  metadata jsonb, created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);

CREATE TABLE reviewed_projections (
  reviewed_projection_id uuid PRIMARY KEY, space_id uuid NOT NULL, project_id uuid NOT NULL,
  frame_id uuid NOT NULL, status projection_status NOT NULL, data json,
  validation_result json, review_notes text, source_projection_ids json,
  space_version integer NOT NULL, reviewed_at timestamptz,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
);
CREATE INDEX ix_reviewed_projection_space_project
  ON reviewed_projections (space_id, project_id);

CREATE TABLE spaces (
  space_id uuid PRIMARY KEY, name varchar(255) NOT NULL UNIQUE, description text,
  domain varchar(255) NOT NULL, extraction_schema jsonb NOT NULL, system_prompt text NOT NULL,
  field_descriptions jsonb NOT NULL, version integer NOT NULL,
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);
"""


def upgrade() -> None:
    op.execute(BASELINE_SQL)


def downgrade() -> None:
    for table in (
        "spaces", "reviewed_projections", "research_projects", "projections",
        "project_assets", "processing_logs", "processed_assets", "knowledge_frames",
        "graph_element_reviews", "feedbacks", "extraction_passes", "assets",
    ):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
    for enum_name in (
        "projection_status", "processing_type", "processing_status", "frame_status",
        "feedback_status",
    ):
        op.execute(f'DROP TYPE IF EXISTS "{enum_name}"')
