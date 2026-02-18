"""Scout service package — on-demand audit runs against connected repos.

Sub-modules
-----------
- quick_scan : shallow (quick) scans via GitHub API
- deep_scan  : full project intelligence with architecture mapping
- dossier_builder : LLM-powered dossier generation & score history
- _utils     : shared helpers (_build_check_list, _serialize_run)

All public names are re-exported here so that existing ``from
app.services.scout_service import X`` (via thin facade) keeps working.
"""

from ._utils import _build_check_list, _serialize_run
from .deep_scan import (
    _DEEP_SCAN_MAX_BYTES,
    _DEEP_SCAN_MAX_FILES,
    _ENTRY_FILENAMES,
    _KEY_FILENAMES,
    _complete_deep_scan_empty,
    _execute_deep_scan,
    _select_key_files,
    _send_deep_progress,
    start_deep_scan,
)
from .dossier_builder import (
    _DOSSIER_SYSTEM_PROMPT,
    _generate_dossier,
    get_scout_dossier,
    get_scout_score_history,
)
from .quick_scan import (
    _complete_with_no_changes,
    _execute_scout,
    get_scout_detail,
    get_scout_history,
    start_scout_run,
)

__all__ = [
    # quick_scan
    "start_scout_run",
    "_execute_scout",
    "_complete_with_no_changes",
    "get_scout_history",
    "get_scout_detail",
    # deep_scan
    "start_deep_scan",
    "_send_deep_progress",
    "_execute_deep_scan",
    "_complete_deep_scan_empty",
    "_select_key_files",
    "_DEEP_SCAN_MAX_FILES",
    "_DEEP_SCAN_MAX_BYTES",
    "_KEY_FILENAMES",
    "_ENTRY_FILENAMES",
    # dossier_builder
    "_DOSSIER_SYSTEM_PROMPT",
    "_generate_dossier",
    "get_scout_dossier",
    "get_scout_score_history",
    # _utils
    "_build_check_list",
    "_serialize_run",
]
