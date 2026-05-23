# Vulture whitelist — intentionally-retained names that vulture would
# otherwise flag as dead code.
#
# Each entry needs a one-line justification linking back to the
# source comment that documents the intent. New entries land via PR
# review only — drift here is what the gate exists to catch.

# bqemulator.jobs.executor.execute_query_job: ``use_cache`` is a
# reserved kwarg for future query-cache integration. The source
# already has ``# noqa: ARG001`` next to it documenting the intent;
# vulture's "unused variable" check fires independently of ruff's
# unused-argument rule, so the name needs to be referenced here too.
use_cache  # noqa: F821, B018 - vulture whitelist sentinel
