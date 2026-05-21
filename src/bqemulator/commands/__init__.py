"""CLI subcommand implementations.

Each submodule implements one ``bqemulator`` subcommand:

* :mod:`bqemulator.commands.import_project` — mirror schemas from a real
  BigQuery project into the local catalog (requires the ``import`` extra).
* :mod:`bqemulator.commands.export` — dump the catalog and table rows as
  portable YAML + Parquet files.
* :mod:`bqemulator.commands.seed` — load an export directory back into a
  local persistent catalog (complement of ``import``).
* :mod:`bqemulator.commands.backup` — capture the persistent DuckDB
  database to a portable directory.
* :mod:`bqemulator.commands.restore` — restore a backup back into a
  persistent ``data_dir``.

These modules are deliberately kept out of the runtime hot path: the
``start`` server entry point never imports them. CLI subcommands deferred-
import them when invoked, which keeps cold-start fast and lets the
``import`` extra remain optional.
"""

from __future__ import annotations

__all__: list[str] = []
