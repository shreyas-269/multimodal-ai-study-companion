from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.access import get_readable_notebook, is_valid_notebook_id
from app.auth import CurrentUser
from app.db.notebooks import (
    DEMO_NOTEBOOK_ID,
    create_notebook,
    get_notebook_snapshot,
    list_owner_notebook_snapshots,
)
from app.models.notebook import Notebook, NotebookCreate, NotebookList

router = APIRouter(tags=["notebooks"])


@router.post("/notebooks", status_code=201, name="create")
def create(body: NotebookCreate, current_user: CurrentUser) -> Notebook:
    """Create a new notebook with default values."""
    doc_dict = create_notebook(name=body.name, owner_uid=current_user.uid)
    return Notebook.model_validate(doc_dict)


# Named list_notebooks so the builtin list isn't shadowed; name="list" keeps the operation ID notebooks_list (DECISIONS 2026-10-04).  # noqa: E501
@router.get("/notebooks", name="list")
def list_notebooks(
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> NotebookList:
    """List notebooks owned by caller newest first, with demo notebook prepended on page 1."""
    cursor_snapshot = None
    if cursor is not None:
        if not is_valid_notebook_id(cursor):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Invalid cursor format"},
            )
        cursor_snapshot = get_notebook_snapshot(cursor)
        if not cursor_snapshot.exists:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Cursor notebook not found"},
            )
        cursor_data = cursor_snapshot.to_dict() or {}
        if cursor_data.get("owner_uid") != current_user.uid:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Cursor does not belong to current user"},
            )

    raw = list_owner_notebook_snapshots(
        owner_uid=current_user.uid,
        limit=limit,
        cursor_snapshot=cursor_snapshot,
    )
    has_more = len(raw) > limit
    items = [
        Notebook.model_validate({"id": doc.id, **(doc.to_dict() or {})})
        for doc in raw[:limit]
        if doc.id != DEMO_NOTEBOOK_ID
    ]
    next_cursor = raw[limit - 1].id if has_more else None

    # Prepend demo notebook on page 1 only if it exists and is_demo is True
    if cursor is None:
        demo_doc = get_notebook_snapshot(DEMO_NOTEBOOK_ID)
        if demo_doc.exists:
            demo_data = demo_doc.to_dict() or {}
            if demo_data.get("is_demo") is True:
                items.insert(
                    0,
                    Notebook.model_validate({"id": demo_doc.id, **demo_data}),
                )

    return NotebookList(items=items, next_cursor=next_cursor)


@router.get("/notebooks/{nb}", name="get")
def get(notebook: Annotated[Notebook, Depends(get_readable_notebook)]) -> Notebook:
    """Get a readable notebook by ID."""
    return notebook
