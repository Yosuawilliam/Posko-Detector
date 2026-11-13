import os
import secrets
from fastapi import Header, HTTPException, status

ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "posko-secret-admin-2026")


def verify_admin_auth(x_admin_token: str | None = Header(default=None)) -> str:
    if not isinstance(x_admin_token, str) or not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token header",
        )
    return x_admin_token
