from fastapi import APIRouter

from ...schemas import OkResponse

router = APIRouter(tags=["System"])


@router.get("/health", summary="Health check", response_model=OkResponse)
def health():
    """Returns `{"ok": true}` when the service is running. No authentication needed."""
    return {"ok": True}
