from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE work_orders
        ADD COLUMN status TEXT NOT NULL DEFAULT 'planned'
        CONSTRAINT work_orders_status_check
        CHECK (status IN ('planned', 'in_progress', 'completed'))
    """)


def downgrade():
    op.drop_column("work_orders", "status")
