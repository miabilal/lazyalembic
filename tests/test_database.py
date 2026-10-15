import sqlite3

import pytest
from conftest import PLAYGROUND
from sqlalchemy.engine import make_url

from lazyalembic import database
from lazyalembic.chain import load_chain
from lazyalembic.database import (
    ENV_VAR,
    DatabaseError,
    compare,
    describe,
    find_database_url,
    read_heads,
)


def sqlite_db(path, *heads, with_table=True):
    con = sqlite3.connect(path)
    if with_table:
        con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        con.executemany("INSERT INTO alembic_version VALUES (?)", [(h,) for h in heads])
    con.commit()
    con.close()
    return make_url(f"sqlite:///{path}")


def ini_with_url(tmp_path, url):
    ini = tmp_path / "alembic.ini"
    ini.write_text(f"[alembic]\nscript_location = migrations\nsqlalchemy.url = {url}\n")
    return ini



def test_flag_beats_env_var_beats_ini(tmp_path, monkeypatch):
    ini = ini_with_url(tmp_path, "sqlite:///from_ini.db")
    monkeypatch.setenv(ENV_VAR, "sqlite:////tmp/from_env.db")

    assert find_database_url(ini, override="sqlite:////tmp/from_flag.db").database == "/tmp/from_flag.db"
    assert find_database_url(ini).database == "/tmp/from_env.db"
    monkeypatch.delenv(ENV_VAR)
    assert find_database_url(ini).database == str(tmp_path / "from_ini.db")


def test_alembic_init_placeholder_means_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    ini = ini_with_url(tmp_path, "driver://user:pass@localhost/dbname")
    assert find_database_url(ini) is None
    assert find_database_url(None) is None


def test_playground_ini_points_at_its_sqlite_file(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    url = find_database_url(PLAYGROUND / "alembic.ini")
    assert url.database == str(PLAYGROUND / "playground.db")


def test_bad_url_is_explained():
    with pytest.raises(DatabaseError, match="isn't a valid SQLAlchemy URL"):
        find_database_url(None, override="not a url")


def test_label_never_shows_user_or_password():
    url = make_url("mysql+pymysql://mia:s3cret@127.0.0.1:3306/nysmah_db")
    assert describe(url) == "127.0.0.1/nysmah_db"



def test_reads_the_recorded_revision(tmp_path):
    url = sqlite_db(tmp_path / "app.db", "c3d5e4f6a7b8")
    assert read_heads(url) == ("c3d5e4f6a7b8",)


def test_database_without_alembic_table_has_nothing_applied(tmp_path):
    url = sqlite_db(tmp_path / "fresh.db", with_table=False)
    assert read_heads(url) == ()


def test_missing_sqlite_file_is_reported_and_not_created(tmp_path):
    path = tmp_path / "nope.db"
    with pytest.raises(DatabaseError, match="doesn't exist"):
        read_heads(make_url(f"sqlite:///{path}"))
    assert not path.exists()


def test_missing_driver_says_what_to_install(monkeypatch):
    def no_driver(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'MySQLdb'", name="MySQLdb")

    monkeypatch.setattr(database, "create_engine", no_driver)
    with pytest.raises(DatabaseError, match="--with 'mysqlclient'"):
        read_heads(make_url("mysql+mysqldb://u:p@localhost/db"))


def test_unknown_database_type():
    with pytest.raises(DatabaseError, match="doesn't know the database type"):
        read_heads(make_url("nosuchdb://u:p@localhost/db"))



def test_database_behind_one_branch_point():
    state = compare(load_chain(PLAYGROUND), ("c3d5e4f6a7b8",))
    assert state.applied == {"c3d5e4f6a7b8", "b2e4d3c5f6a7", "a1f3c2d4e5b6"}
    assert set(state.pending) == {"d4e6f5a7b8c9", "e5f7a6b8c9d0"}
    assert state.unknown == ()


def test_database_on_both_ends_is_up_to_date():
    state = compare(load_chain(PLAYGROUND), ("d4e6f5a7b8c9", "e5f7a6b8c9d0"))
    assert state.pending == ()
    assert len(state.applied) == 5


def test_empty_database_has_everything_pending():
    state = compare(load_chain(PLAYGROUND), ())
    assert len(state.pending) == 5
    assert state.applied == frozenset()


def test_revision_from_another_branch_is_flagged():
    state = compare(load_chain(PLAYGROUND), ("abc123notours",))
    assert state.unknown == ("abc123notours",)
    assert state.applied == frozenset()
