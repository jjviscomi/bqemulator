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


# -----------------------------------------------------------------
# Early-bail branches of the scanner — pinned individually so a
# refactor doesn't silently regress one of them.
# -----------------------------------------------------------------


def test_alter_without_table_keyword_passthrough() -> None:
    # ``ALTER <not TABLE>`` — TABLE-required bail.
    sql = "ALTER VIEW v SET OPTIONS()"
    assert rewrite_alter_table_set_options(sql) == sql


def test_alter_table_without_set_clause_passthrough() -> None:
    # Walks past TABLE looking for SET, runs out of tokens.
    sql = "ALTER TABLE t"
    assert rewrite_alter_table_set_options(sql) == sql


def test_set_without_options_passthrough() -> None:
    # SET is found but the next token isn't OPTIONS.
    sql = "ALTER TABLE t SET DEFAULT COLLATE 'und:ci'"
    assert rewrite_alter_table_set_options(sql) == sql


def test_options_without_open_paren_passthrough() -> None:
    # OPTIONS matched but no ``(`` follows.
    sql = "ALTER TABLE t SET OPTIONS"
    assert rewrite_alter_table_set_options(sql) == sql


def test_options_with_unterminated_paren_passthrough() -> None:
    # ``OPTIONS(`` without the closing ``)``.
    sql = "ALTER TABLE t SET OPTIONS(description='no closing"
    assert rewrite_alter_table_set_options(sql) == sql


def test_trailing_garbage_after_close_paren_passthrough() -> None:
    # Valid up through ``)`` but more SQL follows.
    sql = "ALTER TABLE t SET OPTIONS() AND THEN SOME"
    assert rewrite_alter_table_set_options(sql) == sql


def test_trailing_semicolon_accepted() -> None:
    # The optional ``;`` is consumed before the EOF check.
    sql = "ALTER TABLE t SET OPTIONS();"
    assert rewrite_alter_table_set_options(sql) == "SELECT 1"


def test_comment_only_input_passthrough() -> None:
    # Input is entirely comments + whitespace — bail after
    # the comment-stripper consumes everything.
    sql = "/* just a comment */"
    assert rewrite_alter_table_set_options(sql) == sql


def test_empty_input_passthrough() -> None:
    assert rewrite_alter_table_set_options("") == ""


def test_unterminated_block_comment_consumed_to_eof() -> None:
    # ``_strip_leading_comments`` returns ``n`` (consumed the whole
    # string) when ``/*`` has no closing ``*/``.
    sql = "/* never closed"
    assert rewrite_alter_table_set_options(sql) == sql
