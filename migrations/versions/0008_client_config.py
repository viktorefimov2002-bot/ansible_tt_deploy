"""Encrypted official generator output for client delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0008_client_config"
down_revision = "0007_client_auth"
branch_labels = depends_on = None


def upgrade():
    op.add_column("device_credentials", sa.Column("config_ciphertext", sa.LargeBinary()))


def downgrade():
    op.drop_column("device_credentials", "config_ciphertext")
