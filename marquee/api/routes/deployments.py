"""Deployments: the audit of every write to a movie folder, and restoring one. Every route
needs the API key."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from marquee.api.schemas.jobs import JobItem
from marquee.api.schemas.runs import DeploymentItem, DeploymentList
from marquee.api.sessions import Session
from marquee.artwork import deploy
from marquee.db.models import Deployment
from marquee.jobs.handlers.poster_write import WriteRefusedError, enqueue_restore

router = APIRouter(prefix="/deployments", tags=["deployments"])


@router.get("")
async def list_deployments(
    session: Session,
    movie_id: int | None = None,
    page: Annotated[int, Query(ge=1, description="Page number, from 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DeploymentList:
    """Every write Marquee made to a movie folder, newest first."""
    result = await deploy.list_deployments(
        session, movie_id=movie_id, page=page, page_size=page_size
    )
    return DeploymentList(
        total=result.total,
        page=page,
        page_size=page_size,
        items=[DeploymentItem.build(row) for row in result.items],
    )


@router.post(
    "/{deployment_id}/restore",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"description": "The same request is the movie's newest write and still queued"},
        404: {"description": "No such deployment"},
        409: {"description": "The deployment cannot be restored"},
    },
)
async def queue_restore(session: Session, deployment_id: int, response: Response) -> JobItem:
    """Queue a restore that puts back what was in the folder before this deployment (any
    deployment of a movie, not only the newest)."""
    if await session.get(Deployment, deployment_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deployment not found")
    try:
        job, created = await enqueue_restore(session, deployment_id)
    except WriteRefusedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return JobItem.model_validate(job)
