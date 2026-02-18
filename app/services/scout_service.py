"""Scout service -- thin re-export facade.

All logic now lives in the `app.services.scout` package:

- `scout.quick_scan`     -- shallow quick scans
- `scout.deep_scan`      -- full project intelligence
- `scout.dossier_builder`-- LLM dossier & score history
- `scout._utils`         -- shared helpers

This file exists solely for backward compatibility with existing imports::

    from app.services.scout_service import start_scout_run
"""

from app.services.scout import *  # noqa: F401,F403
