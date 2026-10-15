import textwrap

import pytest
from sqlalchemy.engine import make_url

from lazyalembic.risks import (
    BLOCKS,
    CHECK,
    DATA,
    REBUILD,
    SLOW,
    find_risks,
    rules_apply,
)


def risks_in(tmp_path, upgrade_body, downgrade_body="pass", header=""):
    path = tmp_path / "abc_change.py"
    path.write_text(
        f"{header}import sqlalchemy as sa\nfrom alembic import op\n\n"
        "revision = 'abc'\ndown_revision = None\n\n\n"
        f"def upgrade():\n{textwrap.indent(textwrap.dedent(upgrade_body), '    ')}\n\n"
        f"def downgrade():\n    {downgrade_body}\n"
    )
    return find_risks(path)


def levels(risks):
    return [r.level for r in risks]



def test_type_change_copies_the_table(tmp_path):
    risks = risks_in(tmp_path, """
        op.alter_column("orders", "notes", existing_type=sa.String(100), type_=sa.Text())
    """)
    assert levels(risks) == [BLOCKS]
    assert risks[0].table == "orders"
    assert risks[0].what == "Changes the type of orders.notes"
    assert "copies the whole orders table" in risks[0].why


def test_type_change_without_existing_type_asks_for_it(tmp_path):
    risks = risks_in(tmp_path, 'op.alter_column("orders", "notes", type_=sa.Text())')
    assert levels(risks) == [BLOCKS]
    assert "existing_type=" in risks[0].why


@pytest.mark.parametrize(
    ("old", "new", "blocks"),
    [
        (50, 63, False),    # both under 256 bytes in utf8mb4: in place
        (50, 64, True),     # crosses 255 bytes: table copy
        (100, 255, False),  # both over: in place
        (255, 100, True),   # shrinking: table copy
        (100, 100, False),
    ],
)
def test_varchar_length_rules(tmp_path, old, new, blocks):
    risks = risks_in(tmp_path, f"""
        op.alter_column("users", "name", existing_type=sa.String(length={old}), type_=sa.String({new}))
    """)
    assert levels(risks) == ([BLOCKS] if blocks else [])


def test_mysql_varchar_types_count_too(tmp_path):
    risks = risks_in(tmp_path, """
        op.alter_column("users", "name", existing_type=mysql.VARCHAR(length=20), type_=mysql.VARCHAR(length=40))
    """, header="from sqlalchemy.dialects import mysql\n")
    assert levels(risks) == []


@pytest.mark.parametrize(
    ("old", "new", "blocks"),
    [
        ('"new", "paid"', '"new", "paid", "shipped"', False),  # appended: instant
        ('"new", "paid"', '"paid", "new"', True),              # reordered: copy
        ('"new", "paid"', '"new"', True),                      # removed: copy
    ],
)
def test_enum_rules(tmp_path, old, new, blocks):
    risks = risks_in(tmp_path, f"""
        op.alter_column("orders", "status", existing_type=sa.Enum({old}), type_=sa.Enum({new}))
    """)
    assert levels(risks) == ([BLOCKS] if blocks else [])



def test_not_null_rebuilds_and_can_fail(tmp_path):
    risks = risks_in(tmp_path, """
        op.alter_column("orders", "email", existing_type=sa.String(255), nullable=False)
    """)
    assert levels(risks) == [REBUILD]
    assert "required (NOT NULL)" in risks[0].what
    assert "fails partway if any row already has NULL" in risks[0].why


def test_making_nullable_rebuilds(tmp_path):
    risks = risks_in(tmp_path, """
        op.alter_column("orders", "email", existing_type=sa.String(255), nullable=True)
    """)
    assert levels(risks) == [REBUILD]
    assert "NULL in this column" not in risks[0].why


def test_rename_and_default_are_instant(tmp_path):
    risks = risks_in(tmp_path, """
        op.alter_column("orders", "email", new_column_name="contact_email")
        op.alter_column("orders", "status", server_default="new")
    """)
    assert risks == []



def test_plain_add_column_is_instant(tmp_path):
    assert risks_in(tmp_path, 'op.add_column("orders", sa.Column("notes", sa.Text(), nullable=True))') == []


def test_add_column_with_foreign_key_copies_the_table(tmp_path):
    risks = risks_in(tmp_path, """
        op.add_column("orders", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")))
    """)
    assert levels(risks) == [BLOCKS]
    assert "foreign_key_checks" in risks[0].why


def test_stored_generated_column_copies_virtual_does_not(tmp_path):
    stored = risks_in(tmp_path, """
        op.add_column("orders", sa.Column("total", sa.Integer(), sa.Computed("a + b", persisted=True)))
    """)
    virtual = risks_in(tmp_path, """
        op.add_column("orders", sa.Column("total", sa.Integer(), sa.Computed("a + b")))
    """)
    assert levels(stored) == [BLOCKS]
    assert virtual == []


def test_add_column_with_index(tmp_path):
    risks = risks_in(tmp_path, 'op.add_column("orders", sa.Column("ref", sa.String(20), unique=True))')
    assert levels(risks) == [SLOW]
    assert "duplicate" in risks[0].why



def test_create_index_is_slow_not_blocking(tmp_path):
    risks = risks_in(tmp_path, 'op.create_index("ix_orders_email", "orders", ["email"])')
    assert levels(risks) == [SLOW]
    assert risks[0].table == "orders"
    assert risks[0].what == "Adds index ix_orders_email on orders"


def test_fulltext_index_blocks_writes(tmp_path):
    risks = risks_in(tmp_path, """
        op.create_index("ft_name", "products", ["name"], mysql_prefix="FULLTEXT")
    """)
    assert levels(risks) == [BLOCKS]


def test_create_foreign_key_blocks_writes(tmp_path):
    risks = risks_in(tmp_path, """
        op.create_foreign_key("fk_orders_user", "orders", "users", ["user_id"], ["id"])
    """)
    assert levels(risks) == [BLOCKS]
    assert risks[0].table == "orders"


def test_unique_constraint_can_fail_on_duplicates(tmp_path):
    risks = risks_in(tmp_path, 'op.create_unique_constraint("uq_sku", "products", ["sku"])')
    assert levels(risks) == [SLOW]
    assert "duplicate" in risks[0].why


def test_primary_key_changes(tmp_path):
    added = risks_in(tmp_path, 'op.create_primary_key("pk_t", "t", ["id"])')
    dropped = risks_in(tmp_path, 'op.drop_constraint("PRIMARY", "t", type_="primary")')
    dropped_positional = risks_in(tmp_path, 'op.drop_constraint("PRIMARY", "t", "primary")')
    fk_dropped = risks_in(tmp_path, 'op.drop_constraint("fk_x", "t", type_="foreignkey")')
    assert levels(added) == [REBUILD]
    assert levels(dropped) == [BLOCKS]
    assert levels(dropped_positional) == [BLOCKS]
    assert fk_dropped == []



def test_drops_delete_data(tmp_path):
    risks = risks_in(tmp_path, """
        op.drop_column("orders", "legacy_code")
        op.drop_table("old_carts")
    """)
    assert levels(risks) == [DATA, DATA]
    assert [r.what for r in risks] == ["Drops column orders.legacy_code", "Drops table old_carts"]


def test_only_upgrade_is_checked(tmp_path):
    assert risks_in(tmp_path, "pass", downgrade_body='op.drop_table("orders")') == []



def test_raw_sql(tmp_path):
    risks = risks_in(tmp_path, """
        op.execute("UPDATE product SET slug = LOWER(name)")
        op.execute(sa.text("ALTER TABLE orders ADD COLUMN x INT"))
        op.execute(f"ALTER TABLE {'customers'} CONVERT TO CHARACTER SET utf8mb4")
        op.execute("INSERT INTO menu (name) VALUES ('x')")
    """)
    assert levels(risks) == [BLOCKS, CHECK, SLOW]
    assert risks[1].table == "orders"
    assert risks[2].what == "Updates rows in product in one statement"



def test_batch_mode_takes_the_table_from_the_with_block(tmp_path):
    risks = risks_in(tmp_path, """
        with op.batch_alter_table("orders") as batch_op:
            batch_op.alter_column("notes", existing_type=sa.String(50), type_=sa.Text())
            batch_op.create_index("ix_orders_ref", ["ref"])
            batch_op.drop_column("legacy")
    """)
    assert levels(risks) == [BLOCKS, DATA, SLOW]
    assert {r.table for r in risks} == {"orders"}


def test_worst_first_then_by_line(tmp_path):
    risks = risks_in(tmp_path, """
        op.create_index("ix_a", "a", ["x"])
        op.drop_column("b", "y")
        op.alter_column("c", "z", type_=sa.Text())
    """)
    assert levels(risks) == [BLOCKS, DATA, SLOW]
    assert [r.line for r in risks] == [12, 11, 10]


def test_tables_created_in_the_same_migration_are_empty_so_not_risky(tmp_path):
    risks = risks_in(tmp_path, """
        op.create_table("carts", sa.Column("id", sa.Integer(), primary_key=True))
        with op.batch_alter_table("carts") as batch_op:
            batch_op.create_index(batch_op.f("ix_carts_user"), ["user_id"])
        op.create_foreign_key(None, "carts", "users", ["user_id"], ["id"])
        op.create_index(op.f("ix_orders_cart"), "orders", ["cart_id"])
    """)
    assert [r.what for r in risks] == ["Adds index ix_orders_cart on orders"]


def test_unnamed_constraints_read_naturally(tmp_path):
    risks = risks_in(tmp_path, """
        op.create_foreign_key(None, "orders", "users", ["user_id"], ["id"])
        op.create_index(name_var, "orders", ["x"])
    """)
    assert [r.what for r in risks] == [
        "Adds a foreign key on orders",
        "Adds an index on orders",
    ]


def test_unreadable_file_has_no_risks(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text("def upgrade(:\n")
    assert find_risks(path) == []



def test_rules_follow_the_database_type(tmp_path):
    assert rules_apply(make_url("mysql+pymysql://u:p@localhost/db"), [])
    assert rules_apply(make_url("mariadb+pymysql://u:p@localhost/db"), [])
    assert not rules_apply(make_url("postgresql://u:p@localhost/db"), [])
    assert not rules_apply(make_url("sqlite:///x.db"), [])


def test_without_a_database_mysql_features_in_migrations_decide(tmp_path):
    mysql_file = tmp_path / "a.py"
    mysql_file.write_text("from sqlalchemy.dialects import mysql\n")
    plain = tmp_path / "b.py"
    plain.write_text("import sqlalchemy as sa\n")
    engine_kw = tmp_path / "c.py"
    engine_kw.write_text("op.create_table('t', mysql_engine='InnoDB')\n")
    assert rules_apply(None, [plain, mysql_file])
    assert rules_apply(None, [engine_kw])
    assert not rules_apply(None, [plain])
