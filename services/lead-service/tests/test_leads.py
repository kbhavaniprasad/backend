"""
tests/test_leads.py – Unit and integration tests for the /api/v1/leads router.
"""

import uuid

import pytest
from httpx import AsyncClient


TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
HEADERS = {"X-Tenant-ID": TENANT_ID, "X-User-ID": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_lead_payload(**overrides) -> dict:
    base = {
        "source": "manual",
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "+1-555-123-4567",
        "company": "Acme Corp",
        "job_title": "VP Sales",
    }
    base.update(overrides)
    return base


# ── POST / ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_lead_success(client: AsyncClient):
    response = await client.post("/api/v1/leads/", json=make_lead_payload())
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == "John"
    assert data["last_name"] == "Doe"
    assert data["status"] == "new"
    assert data["source"] == "manual"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_lead_requires_contact_method(client: AsyncClient):
    """Should reject leads with neither email nor phone."""
    payload = make_lead_payload()
    payload.pop("email")
    payload.pop("phone")
    response = await client.post("/api/v1/leads/", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_lead_duplicate_external_id(client: AsyncClient):
    """Creating two leads with the same external_id for a tenant returns 409."""
    payload = make_lead_payload(external_id="EXT-001", email="a@b.com")
    r1 = await client.post("/api/v1/leads/", json=payload)
    assert r1.status_code == 201

    r2 = await client.post("/api/v1/leads/", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_create_lead_publishes_kafka_event(client: AsyncClient, mock_kafka):
    await client.post("/api/v1/leads/", json=make_lead_payload(email="kafka@test.com"))
    mock_kafka.publish_lead_created.assert_called_once()


# ── GET / ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_leads_returns_items(client: AsyncClient):
    # Create a lead first
    await client.post("/api/v1/leads/", json=make_lead_payload(email="list@test.com"))

    response = await client.get("/api/v1/leads/")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert data["total"] >= 1
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_list_leads_filter_by_status(client: AsyncClient):
    response = await client.get("/api/v1/leads/?status=new")
    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert item["status"] == "new"


@pytest.mark.asyncio
async def test_list_leads_filter_by_source(client: AsyncClient):
    response = await client.get("/api/v1/leads/?source=manual")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_leads_pagination(client: AsyncClient):
    response = await client.get("/api/v1/leads/?page=1&page_size=2")
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 2


# ── GET /{lead_id} ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_lead_by_id(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/leads/", json=make_lead_payload(email="get@test.com")
    )
    lead_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/leads/{lead_id}")
    assert response.status_code == 200
    assert response.json()["id"] == lead_id


@pytest.mark.asyncio
async def test_get_lead_not_found(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/leads/{fake_id}")
    assert response.status_code == 404


# ── PATCH /{lead_id}/status ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_lead_status(client: AsyncClient, mock_kafka):
    create_resp = await client.post(
        "/api/v1/leads/", json=make_lead_payload(email="status@test.com")
    )
    lead_id = create_resp.json()["id"]

    response = await client.patch(
        f"/api/v1/leads/{lead_id}/status",
        json={"status": "contacted", "reason": "Called via phone"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "contacted"
    assert data["contacted_at"] is not None


@pytest.mark.asyncio
async def test_update_lead_status_noop(client: AsyncClient):
    """Transitioning to the same status should return 400."""
    create_resp = await client.post(
        "/api/v1/leads/", json=make_lead_payload(email="noop@test.com")
    )
    lead_id = create_resp.json()["id"]

    response = await client.patch(
        f"/api/v1/leads/{lead_id}/status",
        json={"status": "new"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_status_publishes_kafka_event(client: AsyncClient, mock_kafka):
    create_resp = await client.post(
        "/api/v1/leads/", json=make_lead_payload(email="kafkastatus@test.com")
    )
    lead_id = create_resp.json()["id"]

    await client.patch(
        f"/api/v1/leads/{lead_id}/status",
        json={"status": "interested"},
    )
    mock_kafka.publish_lead_status_changed.assert_called_once()


# ── GET /{lead_id}/history ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_lead_history(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/leads/", json=make_lead_payload(email="history@test.com")
    )
    lead_id = create_resp.json()["id"]

    await client.patch(
        f"/api/v1/leads/{lead_id}/status", json={"status": "contacted"}
    )
    await client.patch(
        f"/api/v1/leads/{lead_id}/status", json={"status": "interested"}
    )

    response = await client.get(f"/api/v1/leads/{lead_id}/history")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 2
    assert history[0]["old_status"] == "new"
    assert history[0]["new_status"] == "contacted"
    assert history[1]["old_status"] == "contacted"
    assert history[1]["new_status"] == "interested"


# ── GET /stats/summary ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_summary(client: AsyncClient):
    response = await client.get("/api/v1/leads/stats/summary")
    assert response.status_code == 200
    data = response.json()
    assert "counts" in data
    assert "total" in data
    assert "new" in data["counts"]


# ── POST /bulk ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_create_leads(client: AsyncClient):
    payload = {
        "source": "crm",
        "leads": [
            {
                "source": "crm",
                "first_name": f"Lead{i}",
                "last_name": "Test",
                "email": f"bulklead{i}@test.com",
            }
            for i in range(5)
        ],
    }
    response = await client.post("/api/v1/leads/bulk", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["created"] == 5
    assert data["failed"] == 0
    assert len(data["lead_ids"]) == 5


# ── Health endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    # Degraded is acceptable in test (Kafka mock may not have _producer attr)
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert data["service"] == "lead-service"
