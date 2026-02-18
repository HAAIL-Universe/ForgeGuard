"""Project service -- thin re-export from ``app.services.project`` package.

All logic lives in sub-modules:

* ``project/questionnaire.py``  -- multi-turn chat state machine
* ``project/contract_generator.py`` -- LLM-based contract generation
* ``project/__init__.py``       -- CRUD operations

This file exists solely so that ``from app.services.project_service import X``
continues to work for existing callers (routers, tests, etc.).
"""

from app.services.project import *  # noqa: F401,F403
