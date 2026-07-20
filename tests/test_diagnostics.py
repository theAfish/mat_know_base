from mkb.web.diagnostics import readiness_report


def test_readiness_reports_all_healthy_components():
    report = readiness_report({
        "database": lambda: {"ok": True, "revision": "head"},
        "object_storage": lambda: {"ok": True, "bucket_count": 4},
        "worker": lambda: {"ok": True},
    })

    assert report["status"] == "ready"
    assert set(report["components"]) == {"database", "object_storage", "worker"}


def test_readiness_categorizes_dependency_errors_without_exposing_details():
    def unavailable():
        raise RuntimeError("password=recognizable-secret")

    report = readiness_report({"database": unavailable})

    assert report == {
        "status": "not_ready",
        "components": {
            "database": {
                "ok": False,
                "category": "dependency_unavailable",
                "message": "RuntimeError",
            }
        },
    }
