from fastapi import APIRouter

from mkb.web.dependencies import get_knowledge_base
from mkb.web._models import GraphReviewRequest

router = APIRouter()


@router.get("/api/graph")
def get_graph(project_id: str | None = None):
    return get_knowledge_base().materials.graph.get(project_id=project_id)


@router.get("/api/graph/review-counts")
def get_graph_review_counts():
    return get_knowledge_base().materials.graph.review_counts()


@router.post("/api/graph/review")
def review_graph(body: GraphReviewRequest):
    job = get_knowledge_base().jobs.submit_action(
        "review_graph", mode=body.mode, seed_count=body.seed_count
    )
    return {"job_id": str(job.id)}


@router.post("/api/graph/clear")
def clear_graph():
    return get_knowledge_base().materials.graph.clear()
