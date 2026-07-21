from fastapi import APIRouter

from mkb import api
from mkb.web.dependencies import get_knowledge_base
from mkb.web._models import GraphReviewRequest

router = APIRouter()


@router.get("/api/graph")
def get_graph(project_id: str | None = None):
    return api.get_knowledge_graph(project_id=project_id)


@router.get("/api/graph/review-counts")
def get_graph_review_counts():
    return api.get_graph_review_counts()


@router.post("/api/graph/review")
def review_graph(body: GraphReviewRequest):
    job = get_knowledge_base().jobs.submit_action(
        "review_graph", mode=body.mode, seed_count=body.seed_count
    )
    return {"job_id": str(job.id)}


@router.post("/api/graph/clear")
def clear_graph():
    return api.clear_knowledge_graphs()
