"""Translation rule registry.

Each ``TranslationRule`` subclass in this package is auto-registered
when the module is imported. The :class:`SQLTranslator` calls
:func:`get_all_rules` to obtain the full rule set.

Adding a rule:

1. Create ``rules/<group>.py`` with one or more ``TranslationRule``
   subclasses.
2. Import the module here so it registers on package load.
3. Add unit tests in ``tests/unit/sql/rules/test_<group>.py``.

See ``docs/architecture/contributing/adding-sql-functions.md`` for the
full walkthrough.
"""

from __future__ import annotations

from bqemulator.sql.rules._base import TranslationRule

_REGISTRY: list[type[TranslationRule]] = []


def register(rule_cls: type[TranslationRule]) -> type[TranslationRule]:
    """Class decorator that registers a rule with the global registry."""
    _REGISTRY.append(rule_cls)
    return rule_cls


def get_all_rules() -> list[TranslationRule]:
    """Return instantiated copies of every registered rule."""
    return [cls() for cls in _REGISTRY]


# Rule modules — imported AFTER register() is defined so the
# @register decorator is available at class-definition time.
from bqemulator.sql.rules import aggregate_types as _aggregate_types  # noqa: E402, F401
from bqemulator.sql.rules import array_helpers as _array_helpers  # noqa: E402, F401
from bqemulator.sql.rules import datetime_semantics as _datetime_semantics  # noqa: E402, F401
from bqemulator.sql.rules import interval_rules as _interval_rules  # noqa: E402, F401
from bqemulator.sql.rules import iso_date_parts as _iso_date_parts  # noqa: E402, F401
from bqemulator.sql.rules import json_helpers as _json_helpers  # noqa: E402, F401
from bqemulator.sql.rules import misc_helpers as _misc_helpers  # noqa: E402, F401
from bqemulator.sql.rules import numeric_types as _numeric_types  # noqa: E402, F401
from bqemulator.sql.rules import range_rules as _range_rules  # noqa: E402, F401
from bqemulator.sql.rules import reciprocal_trig as _reciprocal_trig  # noqa: E402, F401
from bqemulator.sql.rules import safe_math as _safe_math  # noqa: E402, F401
from bqemulator.sql.rules import spatial as _spatial  # noqa: E402, F401
from bqemulator.sql.rules import string_helpers as _string_helpers  # noqa: E402, F401

__all__ = ["TranslationRule", "get_all_rules", "register"]
