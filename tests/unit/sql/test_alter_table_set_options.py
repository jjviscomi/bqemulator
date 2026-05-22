"""Pin the dbt-bigquery integration fixes."""

from bqemulator.sql.rewriter.alter_table_set_options import (
    rewrite_alter_table_set_options,
)


def test_bare_alter_table_set_options() -> None:
    sql = "alter table `proj`.`ds`.`t` set OPTIONS()"
    assert rewrite_alter_table_set_options(sql) == "SELECT 1"


def test_alter_table_set_options_with_body() -> None:
    sql = 'ALTER TABLE `p`.`d`.`t` SET OPTIONS(description="x", expiration_timestamp=NULL)'
    assert rewrite_alter_table_set_options(sql) == "SELECT 1"


def test_alter_table_set_options_after_dbt_comment() -> None:
    sql = (
        '/* {"app": "dbt", "node_id": "seed.x"} */\n\n'
        "    alter table `bqemu-demo`.`dbt_local_raw`.`customers` set OPTIONS()\n  "
    )
    assert rewrite_alter_table_set_options(sql) == "SELECT 1"


def test_alter_table_set_options_after_line_comment() -> None:
    sql = "-- dbt comment\nALTER TABLE p.d.t SET OPTIONS()"
    assert rewrite_alter_table_set_options(sql) == "SELECT 1"


def test_non_alter_passthrough() -> None:
    sql = "SELECT * FROM t"
    assert rewrite_alter_table_set_options(sql) == sql


def test_alter_other_clause_passthrough() -> None:
    sql = "ALTER TABLE t ADD COLUMN x INT64"
    assert rewrite_alter_table_set_options(sql) == sql
