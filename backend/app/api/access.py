import re

from fastapi import HTTPException

from app.auth import CurrentUser
from app.db.notebooks import get_notebook_snapshot
from app.models.notebook import Notebook


def is_valid_notebook_id(value: str) -> bool:
    """Validate notebook ID or cursor format (1-128 chars of letters, digits, _, -)."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value))


def get_readable_notebook(nb: str, current_user: CurrentUser) -> Notebook:
    """Return notebook if caller is owner or is_demo is true; otherwise 404."""
    if not is_valid_notebook_id(nb):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Notebook not found"},
        )

    doc = get_notebook_snapshot(nb)
    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Notebook not found"},
        )

    data = doc.to_dict() or {}
    notebook = Notebook.model_validate({"id": doc.id, **data})
    if notebook.owner_uid == current_user.uid or notebook.is_demo:
        return notebook

    raise HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "Notebook not found"},
    )


def get_owned_notebook(nb: str, current_user: CurrentUser) -> Notebook:
    """Return notebook for owner only; 404 if not readable, 403 if readable but not owned."""
    notebook = get_readable_notebook(nb, current_user)
    if notebook.owner_uid != current_user.uid:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Notebook is not owned by current user"},
        )
    return notebook
