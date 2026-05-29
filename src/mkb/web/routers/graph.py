from fastapi import APIRouter

from mkb import api
from mkb.web._models import GraphReviewRequest
from mkb.web._state import jobs

router = APIRouter()


@router.get("/api/graph")
def get_graph(project_id: str | None = None):
    return api.get_knowledge_graph(project_id=project_id)


@router.get("/api/graph/review-counts")
def get_graph_review_counts():
    return api.get_graph_review_counts()


@router.post("/api/graph/review")
def review_graph(body: GraphReviewRequest):
    job_id = jobs.start_job(
        kind="graph_review",
        label="Graph Review",
        target=api.review_knowledge_graph,
        kwargs={"mode": body.mode, "seed_count": body.seed_count},
    )
    return {"job_id": job_id}


@router.post("/api/graph/clear")
def clear_graph():
    return api.clear_knowledge_graphs()
