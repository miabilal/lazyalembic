import pytest
from conftest import PLAYGROUND, write_migration

from lazyalembic.chain import ChainError, load_chain


def by_rev(chain):
    return {m.revision: m for m in chain.migrations}


def test_playground_chain_is_newest_first_with_two_heads():
    chain = load_chain(PLAYGROUND)

    revs = [m.revision for m in chain.migrations]
    assert len(revs) == 5
    assert set(revs[:2]) == {"d4e6f5a7b8c9", "e5f7a6b8c9d0"}
    assert revs[2:] == ["c3d5e4f6a7b8", "b2e4d3c5f6a7", "a1f3c2d4e5b6"]
    assert set(chain.heads) == {"d4e6f5a7b8c9", "e5f7a6b8c9d0"}
    assert chain.project_name == "playground"
    assert chain.problems == []

    migrations = by_rev(chain)
    assert migrations["c3d5e4f6a7b8"].is_branch_point
    assert migrations["a1f3c2d4e5b6"].is_base
    assert migrations["d4e6f5a7b8c9"].is_head
    assert migrations["a1f3c2d4e5b6"].message == "Create products"
    assert migrations["a1f3c2d4e5b6"].created == "2026-09-01"
    assert migrations["e5f7a6b8c9d0"].down_revisions == ("c3d5e4f6a7b8",)


@pytest.mark.parametrize("start", ["alembic.ini", ".", "migrations"])
def test_finds_chain_from_ini_project_or_migrations_folder(start):
    chain = load_chain(PLAYGROUND / start)
    assert len(chain.migrations) == 5


def test_flask_migrate_layout_without_script_location(project):
    # Flask-Migrate's alembic.ini has no script_location
    (project / "migrations" / "alembic.ini").write_text("[alembic]\n\n[loggers]\nkeys = root\n")
    write_migration(project, "aaa", None)
    write_migration(project, "bbb", "aaa")

    for start in (project, project / "migrations", project / "migrations" / "alembic.ini"):
        chain = load_chain(start)
        assert [m.revision for m in chain.migrations] == ["bbb", "aaa"]


def test_migration_code_is_never_run(project):
    write_migration(
        project, "aaa", None,
        extra="import module_that_is_not_installed\nraise SystemExit('ran!')",
    )
    chain = load_chain(project)
    assert [m.revision for m in chain.migrations] == ["aaa"]


def test_merge_and_split_are_detected(project):
    write_migration(project, "aaa", None)
    write_migration(project, "bbb", "aaa")
    write_migration(project, "ccc", "aaa")
    write_migration(project, "ddd", ("bbb", "ccc"), message="Merge heads")

    chain = load_chain(project)
    migrations = by_rev(chain)
    assert chain.heads == ("ddd",)
    assert migrations["ddd"].is_merge
    assert migrations["ddd"].down_revisions == ("bbb", "ccc")
    assert migrations["aaa"].is_branch_point
    assert chain.migrations[0].revision == "ddd"
    assert chain.migrations[-1].revision == "aaa"


def test_unreadable_files_are_reported_and_the_rest_still_load(project):
    write_migration(project, "aaa", None)
    write_migration(project, "bbb", "aaa")
    versions = project / "migrations" / "versions"
    (versions / "computed_id.py").write_text("revision = make_id()\ndown_revision = 'bbb'\n")
    (versions / "broken.py").write_text("revision = 'ccc'\ndef oops(:\n")

    chain = load_chain(project)
    assert [m.revision for m in chain.migrations] == ["bbb", "aaa"]
    assert len(chain.problems) == 2
    assert any("computed_id.py" in p and "not a plain value" in p for p in chain.problems)
    assert any("broken.py" in p and "syntax error" in p for p in chain.problems)


def test_missing_parent_is_reported(project):
    write_migration(project, "aaa", None)
    write_migration(project, "bbb", "deleted_one")

    chain = load_chain(project)
    assert any("deleted_one" in p for p in chain.problems)


def test_non_migration_files_are_skipped(project):
    write_migration(project, "aaa", None)
    versions = project / "migrations" / "versions"
    (versions / "__init__.py").write_text("revision = 'not_me'\n")
    (versions / "helpers.py").write_text("def shared():\n    pass\n")
    (versions / "notes.txt").write_text("revision = 'not_me_either'\n")

    chain = load_chain(project)
    assert [m.revision for m in chain.migrations] == ["aaa"]
    assert chain.problems == []


def test_folder_without_migrations(tmp_path):
    with pytest.raises(ChainError, match="No Alembic migrations found"):
        load_chain(tmp_path)


def test_missing_path(tmp_path):
    with pytest.raises(ChainError, match="does not exist"):
        load_chain(tmp_path / "nope")


def test_empty_versions_folder(project):
    with pytest.raises(ChainError, match="No migrations found"):
        load_chain(project)
