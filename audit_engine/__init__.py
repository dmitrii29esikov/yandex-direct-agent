"""
Dvizhok audita.

Import modulya proverok — eto i est' registratsiya: kazhdaya @register
popadaet v obschee CHECKS. Bez etih importov spisok proverok budet pustym.
"""

from . import checks_direct  # noqa: F401
from . import checks_metrica  # noqa: F401
from . import checks_ytm      # noqa: F401
from . import checks_cross    # noqa: F401

from .base import CHECKS, Diagnosis, Finding  # noqa: F401
from .runner import Ctx, run_audit  # noqa: F401

__all__ = ["run_audit", "Ctx", "Finding", "Diagnosis", "CHECKS"]
