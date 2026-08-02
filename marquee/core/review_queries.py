"""Shared predicates for unresolved poster reviews.

Library status, run eligibility, and the Pipeline Review tabs must agree about
when a completed analysis is ready for a human decision.  Keep the terminal
run rules here so those surfaces cannot drift independently.
"""

from sqlalchemy import or_

from marquee.models import Job, PipelineRun

REVIEW_QUEUE_STATUSES = frozenset({"completed", "flagged_manual"})


def terminal_review_conditions():
    """Conditions for a run that is visible in a Pipeline Review tab.

    Callers must include ``Job`` in the statement, normally with an outer join
    for the legacy unlinked-run allowance.
    """

    return (
        PipelineRun.feedback_event_id.is_(None),
        PipelineRun.status.in_(tuple(REVIEW_QUEUE_STATUSES)),
        or_(PipelineRun.job_id.is_(None), Job.phase == "terminal"),
    )
