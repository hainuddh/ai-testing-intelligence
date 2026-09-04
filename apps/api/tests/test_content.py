from datetime import UTC, datetime

from app.models import ContentItem, Source, User
from app.security import create_access_token, hash_password


def user_headers(db_session, username="viewer", role="viewer"):
    user = User(
        username=username,
        password_hash=hash_password("password"),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def test_content_requires_authentication(client):
    assert client.get("/api/v1/content").status_code == 401


def test_list_filter_and_get_content(client, db_session):
    user, headers = user_headers(db_session)
    first_source = Source(
        name="First",
        source_type="blog",
        languages=["en"],
        topics=[],
        created_by=user.id,
    )
    second_source = Source(
        name="Second",
        source_type="blog",
        languages=["en"],
        topics=[],
        created_by=user.id,
    )
    db_session.add_all([first_source, second_source])
    db_session.flush()
    matching = ContentItem(
        source_id=first_source.id,
        title="Reliable agent testing",
        url="https://example.com/agent-testing",
        summary="A practical guide",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
        analysis_status="analyzed",
        testing_relevance_score=85,
        testing_value_score=90,
        analysis_summary="Testing summary",
        testing_value_analysis="Testing value",
        related_links=[
            {"title": "Regression benchmark", "url": "https://tools.example/benchmark"}
        ],
    )
    db_session.add_all(
        [
            matching,
            ContentItem(
                source_id=second_source.id,
                title="Other article",
                url="https://example.com/other",
                body="Unrelated content",
                analysis_status="filtered",
                testing_relevance_score=20,
                testing_value_score=10,
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(matching)

    response = client.get(
        f"/api/v1/content?source_id={first_source.id}&query=agent&limit=1",
        headers=headers,
    )
    detail = client.get(f"/api/v1/content/{matching.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Reliable agent testing"
    assert response.json()["items"][0]["source_name"] == "First"
    assert detail.status_code == 200
    assert detail.json()["source_id"] == first_source.id
    assert detail.json()["related_links"][0]["title"] == "Regression benchmark"
    assert client.get("/api/v1/content/9999", headers=headers).status_code == 404

    ranged = client.get(
        "/api/v1/content?start_at=2026-08-19T00:00:00Z&end_at=2026-08-21T00:00:00Z",
        headers=headers,
    )
    invalid_range = client.get(
        "/api/v1/content?start_at=2026-08-22T00:00:00Z&end_at=2026-08-21T00:00:00Z",
        headers=headers,
    )

    assert ranged.status_code == 200
    assert ranged.json()["total"] == 1
    assert ranged.json()["items"][0]["id"] == matching.id
    assert invalid_range.status_code == 422

    high_value = client.get("/api/v1/content?min_value_score=95", headers=headers)
    assert high_value.status_code == 200
    assert high_value.json()["total"] == 0


def test_export_selected_content_as_markdown(client, db_session):
    user, headers = user_headers(db_session)
    source = Source(
        name="Quality Engineering Lab",
        source_type="blog",
        languages=["zh-CN"],
        topics=["testing"],
        created_by=user.id,
    )
    db_session.add(source)
    db_session.flush()
    first = ContentItem(
        source_id=source.id,
        title="AI Agent 回归测试实践",
        url="https://example.com/agent-regression",
        analysis_status="analyzed",
        testing_relevance_score=92,
        testing_value_score=88,
        analysis_summary="使用 Agent 辅助维护回归测试。",
        testing_value_analysis="可减少重复维护工作，并扩大变更影响分析范围。",
        applicable_scenarios=["回归测试用例维护", "变更影响分析"],
        adoption_suggestions=["先在低风险模块进行对照试验"],
        analysis_risks=["错误断言可能导致误报"],
        analysis_tags=["AI Agent", "回归测试"],
        related_links=[
            {"title": "Agent evaluation", "url": "https://example.org/evaluation"}
        ],
    )
    second = ContentItem(
        source_id=source.id,
        title="视觉模型 UI 测试",
        url="https://example.com/visual-ui-testing",
        analysis_status="analyzed",
        testing_relevance_score=85,
        testing_value_score=80,
        analysis_summary="视觉模型可辅助识别 UI 视觉回归。",
        testing_value_analysis="适合补充像素差异规则难以覆盖的语义变化。",
        applicable_scenarios=["跨设备视觉回归"],
        adoption_suggestions=["保留基线截图并人工复核模型发现"],
        analysis_risks=["模型判断存在不确定性"],
        analysis_tags=["视觉测试"],
    )
    filtered = ContentItem(
        source_id=source.id,
        title="通用 AI 新闻",
        url="https://example.com/general-ai",
        analysis_status="filtered",
        testing_relevance_score=10,
        testing_value_score=10,
    )
    db_session.add_all([first, second, filtered])
    db_session.commit()

    response = client.post(
        "/api/v1/content/export",
        headers=headers,
        json={"content_ids": [second.id, first.id]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    report = response.text
    assert report.startswith("# 软件测试技术情报报告")
    assert "### 相关链接" in report
    assert "[Agent evaluation](https://example.org/evaluation)" in report
    assert report.index("## 1. 视觉模型 UI 测试") < report.index(
        "## 2. AI Agent 回归测试实践"
    )
    assert "### 测试价值分析" in report
    assert "### 适用测试场景" in report
    assert "### 场景落地建议" in report
    assert "[查看原文](https://example.com/agent-regression)" in report
    assert "共 2 条测试情报" in report

    rejected = client.post(
        "/api/v1/content/export", headers=headers, json={"content_ids": [filtered.id]}
    )
    assert rejected.status_code == 404


def test_content_export_requires_authentication_and_ids(client, db_session):
    assert client.post("/api/v1/content/export", json={"content_ids": [1]}).status_code == 401
    _user, headers = user_headers(db_session)
    assert (
        client.post("/api/v1/content/export", headers=headers, json={"content_ids": []}).status_code
        == 422
    )
    too_many = client.post(
        "/api/v1/content/export",
        headers=headers,
        json={"content_ids": list(range(1, 102))},
    )
    assert too_many.status_code == 422
