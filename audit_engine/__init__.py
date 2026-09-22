"""
Dvizhok audita.

Import modulya proverok — eto i est' registratsiya: kazhdaya @register
popadaet v obschee CHECKS. Bez etih importov spisok proverok budet pustym.
To zhe samoe dlya fikserov (FIXERS).
"""

from . import checks_direct   # noqa: F401
from . import checks_strategy  # noqa: F401
from . import checks_metrica  # noqa: F401
from . import checks_ytm      # noqa: F401
from . import checks_cross    # noqa: F401
from . import checks_reports  # noqa: F401

from .base import CHECKS, Diagnosis, Finding  # noqa: F401
from .fixers import FIXERS, Action            # noqa: F401
from .apply import build_plan, rollback, run_apply  # noqa: F401
from .runner import Ctx, build_context, run_audit   # noqa: F401

__all__ = [
    "run_audit", "run_apply", "rollback", "build_plan", "build_context",
    "Ctx", "Finding", "Diagnosis", "Action", "CHECKS", "FIXERS",
]
