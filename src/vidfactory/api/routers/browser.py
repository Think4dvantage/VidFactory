from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse

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
    return templates.TemplateResponse(
        request,
        "partials/listing.html",
        {"listing": listing, "writable_roots": get_config().writable_roots()},
    )


@router.post("/api/browse/{root}/delete", include_in_schema=False)
async def delete_entries(request: Request, root: str):
    form = await request.form()
    path = str(form.get("path", ""))
    paths = [p for p in form.getlist("paths") if p]
    deleted, errors = 0, []
    for rel in paths:
        try:
            filebrowser.delete(root, str(rel))
            deleted += 1
        except filebrowser.PathNotAllowed as exc:
            errors.append(str(exc))
    logger.info(
        "[VF:browser] delete request root=%s path=%s requested=%d deleted=%d errors=%d",
        root,
        path,
        len(paths),
        deleted,
        len(errors),
    )
    try:
        listing = filebrowser.list_dir(root, path)
    except filebrowser.PathNotAllowed:
        listing = filebrowser.list_dir(root, "")
    return templates.TemplateResponse(
        request,
        "partials/listing.html",
        {"listing": listing, "writable_roots": get_config().writable_roots(), "delete_errors": errors},
    )


@router.get("/api/download/{root}")
def download(root: str, path: str = Query("")):
    try:
        target = filebrowser.resolve(root, path)
    except filebrowser.PathNotAllowed as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "PATH_NOT_ALLOWED", "message": str(exc)}},
        )
    if not target.is_file():
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No file at '{path}' under root '{root}'.",
                    "details": {"root": root, "path": path},
                }
            },
        )
    return FileResponse(target, filename=target.name)


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
