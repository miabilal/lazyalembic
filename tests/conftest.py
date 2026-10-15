from pathlib import Path

import pytest

PLAYGROUND = Path(__file__).parents[1] / "playground"

MIGRATION = '''"""{message}

Revision ID: {rev}
Revises: {down}
Create Date: 2026-10-01 10:00:00.000000

"""
{extra}
revision = {rev!r}
down_revision = {down_repr}
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
'''


@pytest.fixture
def project(tmp_path):
    """A bare Alembic project: migrations/env.py + migrations/versions/."""
    migrations = tmp_path / "migrations"
    (migrations / "versions").mkdir(parents=True)
    (migrations / "env.py").write_text("")
    return tmp_path


def write_migration(project, rev, down, message="Some change", extra=""):
    path = project / "migrations" / "versions" / f"{rev}_change.py"
    down_text = ", ".join(down) if isinstance(down, tuple) else (down or "")
    path.write_text(
        MIGRATION.format(
            message=message, rev=rev, down=down_text, down_repr=repr(down), extra=extra
        )
    )
    return path
