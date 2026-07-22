from fastapi import HTTPException
import pytest

from mkb.services.result import ServiceError, error_result
from mkb.web._helpers import require_service_result, require_service_result_or_not_found


def test_require_service_result_maps_service_error_to_http_exception():
    with pytest.raises(HTTPException) as exc:
        require_service_result(ServiceError("No such project", code="not_found", status_code=404))

    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "No such project"
    assert exc.value.detail["code"] == "not_found"


def test_require_service_result_maps_error_dict_status_code():
    with pytest.raises(HTTPException) as exc:
        require_service_result(error_result("Invalid payload", code="invalid", status_code=422))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid payload"


def test_require_service_result_or_not_found_preserves_legacy_error_dicts():
    with pytest.raises(HTTPException) as exc:
        require_service_result_or_not_found({"error": "Project not found"})

    assert exc.value.status_code == 404
    assert exc.value.detail == "Project not found"

