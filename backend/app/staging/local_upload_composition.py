"""Select the one explicitly configured local-upload runtime."""
from __future__ import annotations

import os


def install_local_upload_runtime() -> bool:
    mode = os.getenv("PU_LOCAL_UPLOAD_RUNTIME", "").strip().lower()
    if mode == "production":
        from app.staging.production_local_upload import install_production_local_upload_runtime
        return install_production_local_upload_runtime()
    if mode:
        raise RuntimeError("unsupported_local_upload_runtime")
    from app.staging.ci_local_upload import install_ci_local_upload_runtime
    return install_ci_local_upload_runtime()
