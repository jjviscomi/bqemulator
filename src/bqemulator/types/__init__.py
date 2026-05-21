"""Phase 9 specialized types: GEOGRAPHY, RANGE<T>, INTERVAL.

Each module in this package isolates the codec / parsing / formatting
concerns for a single specialized BigQuery type. Storage type-mapping
(``storage.type_map``) and Arrow output (``storage.arrow_bridge``) call
into these helpers; the SQL rule engine
(``sql.rules.{spatial,range_rules,interval_rules}``) calls them from
the BigQuery → DuckDB translation pass.

Modules
-------

* ``geography`` — WKT / GeoJSON / WKB encoding helpers and the
  BigQuery ``ST_*`` → DuckDB ``ST_*`` name and signature mapping.
* ``range_type`` — ``RANGE<T>`` ↔ ``STRUCT<start T, "end" T>``
  encoding and the ``RANGE_*`` SQL family.
* ``interval`` — BigQuery interval-literal parsing
  (``'1-2 3 4:5:6.789' YEAR TO SECOND``) and the JUSTIFY family.
"""

from __future__ import annotations
