from pathlib import Path

from mkb.web.api_server import app


def test_react_api_clients_match_fastapi_routes_after_sdk_migration():
    frontend = Path(__file__).resolve().parents[1] / "frontend/src/api"
    contracts = [
        ("projects.ts", "'/projects'", "GET", "/api/projects"),
        ("projects.ts", "`/projects/${id}`", "GET", "/api/projects/{project_id}"),
        ("projects.ts", "`/projects/${id}/process`", "POST", "/api/projects/{project_id}/process"),
        ("frames.ts", "'/frames'", "GET", "/api/frames"),
        ("frames.ts", "`/frames/${projectId}/history`", "GET", "/api/frames/{project_id}/history"),
        ("spaces.ts", "'/spaces'", "GET", "/api/spaces"),
        ("spaces.ts", "`/spaces/${spaceId}`", "PUT", "/api/spaces/{space_id}"),
        ("projections.ts", "'/projections'", "GET", "/api/projections"),
        ("projections.ts", "'/projections/review'", "POST", "/api/projections/review"),
        ("feedback.ts", "'/feedback'", "GET", "/api/feedback"),
        ("feedback.ts", "'/feedback/review'", "POST", "/api/feedback/review"),
        ("graph.ts", "'/graph'", "GET", "/api/graph"),
        ("graph.ts", "'/graph/review'", "POST", "/api/graph/review"),
        ("settings.ts", "'/settings'", "GET", "/api/settings"),
        ("settings.ts", "'/settings'", "PUT", "/api/settings"),
        ("assistant.ts", "'/assistant/chat'", "POST", "/api/assistant/chat"),
        ("jobs.ts", "'/jobs'", "GET", "/api/jobs"),
        ("jobs.ts", "'/jobs/cancel-all'", "POST", "/api/jobs/cancel-all"),
        ("skills.ts", "'/skills'", "GET", "/api/skills"),
        ("skills.ts", "'/skills/upload'", "POST", "/api/skills/upload"),
    ]
    backend_routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }

    for filename, frontend_path, method, backend_path in contracts:
        source = (frontend / filename).read_text(encoding="utf-8")
        assert frontend_path in source, f"Missing React API call in {filename}: {frontend_path}"
        assert (method, backend_path) in backend_routes, (
            f"Missing FastAPI route for {method} {backend_path}"
        )
