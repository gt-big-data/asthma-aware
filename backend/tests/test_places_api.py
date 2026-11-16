"""Places integration boundaries using httpx MockTransport, never billed Google calls."""
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import places as api
from app.auth import get_current_user_id
from app.services import places


def settings(enabled=True):
    return SimpleNamespace(places_enabled=enabled, places_api_key="local-test-only",places_requests_per_minute=5)


def test_places_field_mask_timeout_normalized_response_and_no_content_cache():
    calls=[]
    def respond(request):
        calls.append(request)
        assert request.url == "https://places.googleapis.com/v1/places:searchText"
        assert request.headers["X-Goog-FieldMask"] == "places.id,places.displayName,places.location,places.attributions"
        assert "*" not in request.headers["X-Goog-FieldMask"]
        return httpx.Response(200,json={"places":[{"id":"id-123","displayName":{"text":"Atlanta"},
            "location":{"latitude":33.75,"longitude":-84.39},"attributions":[]}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result=places.search(settings(),"Atlanta",client=client)
        assert result["places"][0]["latitude"] == 33.75
        assert result["provider"] == "Google Places"
        places.search(settings(),"Atlanta",client=client)
    assert len(calls)==2  # Provider content is not silently cached.


@pytest.mark.parametrize("code",[400,401,403,429,500,503])
def test_provider_errors_are_bounded_and_sanitized(code):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(code,text="secret-provider-details"))) as client:
        with pytest.raises(places.PlacesUnavailable) as failure:
            places.search(settings(),"Atlanta",client=client)
        assert "secret-provider" not in str(failure.value)
        assert isinstance(failure.value,places.PlacesRateLimited) == (code==429)


def test_disabled_and_malformed_results_do_not_become_fake_locations():
    class NoCall:
        def post(self,*a,**kw): pytest.fail("disabled integration called Google")
    with pytest.raises(places.PlacesUnavailable):places.search(settings(False),"Atlanta",client=NoCall())
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"places":[{"id":"x"}]}))) as client:
        with pytest.raises(places.PlacesUnavailable):places.search(settings(),"Atlanta",client=client)


def test_per_user_and_global_rate_limit_expires_without_unbounded_identity_cache():
    clock=[0]
    limiter=places.RequestLimiter(per_user=2,total=3,clock=lambda:clock[0])
    limiter.allow("a");limiter.allow("a")
    with pytest.raises(places.PlacesRateLimited):limiter.allow("a")
    limiter.allow("b")
    with pytest.raises(places.PlacesRateLimited):limiter.allow("c")
    clock[0]=61
    limiter.allow("c")
    assert list(limiter.buckets)==["c"]


def test_places_api_requires_auth_validates_query_and_exposes_no_key(monkeypatch):
    app=FastAPI();app.state.settings=settings();app.include_router(api.router)
    monkeypatch.setattr(places,"limiter",places.RequestLimiter())
    monkeypatch.setattr(places,"search",lambda *a,**kw:{"places":[],"provider":"Google Places"})
    with TestClient(app) as client:
        assert client.post("/v1/places/search",json={"query":"Atlanta"}).status_code==401
        app.dependency_overrides[get_current_user_id]=uuid4
        for body in ({"query":" "},{"query":"Atlanta","latitude":33.75},{"query":"Atlanta","fieldMask":"*"},
                     {"query":"Atlanta","latitude":91,"longitude":0}):
            assert client.post("/v1/places/search",json=body).status_code==422
        response=client.post("/v1/places/search",json={"query":"Atlanta"})
        assert response.status_code==200 and response.headers["cache-control"]=="private, no-store"
        assert "local-test-only" not in response.text
