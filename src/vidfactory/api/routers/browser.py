from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from vidfactory.api.auth_deps import require_user
from vidfactory.api.templating import templates
from vidfactory.config import get_config
from vidfactory.core import filebrowser

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/browse/{root}", include_in_schema=False)
def browse_page(request: Request, root: str, path: str = ""):
    logger.info("[VF:browser] page root=%s path=%s", root, path)
    return templates.TemplateResponse(
        request,
        "browser.html",
        {"root": root, "path": path, "roots": list(get_config().mount_roots().keys())},
    )


@router.get("/api/browse/{root}", include_in_schema=False)
def browse_listing(request: Request, root: str, path: str = ""):
    try:
        listing = filebrowser.list_dir(root, path)
    except filebrowser.PathNotAllowed as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "PATH_NOT_ALLOWED", "message": str(exc)}},
        )
    return templates.TemplateResponse(request, "partials/listing.html", {"listing": listing})


@router.get("/api/thumb/{root}")
def thumb(root: str, path: str = Query("")):
    try:
        data = filebrowser.thumbnail(root, path)
    except filebrowser.PathNotAllowed as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "PATH_NOT_ALLOWED", "message": str(exc)}},
        )
    return Response(content=data, media_type="image/jpeg")
