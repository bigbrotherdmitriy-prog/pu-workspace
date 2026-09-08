"""Read-only, explainable MVP4 forecasting.

The package deliberately has no persistence or provider side effects.  The
registered router exposes GET-only, project-authorized projections.
"""

from app.execution_forecast.engine import build_forecast

__all__ = ["build_forecast"]
