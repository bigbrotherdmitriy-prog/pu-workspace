"""Read-only, explainable MVP4 forecasting.

The package deliberately has no persistence or provider side effects.  Its
router is not registered globally: integration is an explicit later step.
"""

from app.execution_forecast.engine import build_forecast

__all__ = ["build_forecast"]
