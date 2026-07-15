"""Anonymous public-profile lookup routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from emercard.modules.card_link_assignments import CardLinkAssignmentRepository
from emercard.modules.location_alerts import (
    BrevoEmailDelivery,
    GoogleReverseGeocoder,
    LocationAlertRequest,
    LocationAlertResponse,
    LocationAlertService,
    MongoLocationAlertAuditRepository,
)
from emercard.modules.profiles import ProfileRepository
from emercard.modules.public_links import (
    PublicAccessLinkRepository,
    PublicProfileLookupService,
    PublicProfileResponse,
)


def build_public_profile_router() -> APIRouter:
    router = APIRouter(tags=["public profile lookup"])

    @router.get("/public/{token}", response_model=PublicProfileResponse)
    async def lookup_public_profile(  # pyright: ignore[reportUnusedFunction]
        token: str,
        service: PublicProfileLookupService = Depends(get_public_profile_lookup_service),  # noqa: B008
    ) -> PublicProfileResponse:
        result = await service.lookup(token)
        return PublicProfileResponse(profile=result.profile)

    @router.post(
        "/public/{token}/location-alert",
        response_model=LocationAlertResponse,
    )
    async def send_location_alert(  # pyright: ignore[reportUnusedFunction]
        token: str,
        payload: LocationAlertRequest,
        request: Request,
        service: LocationAlertService = Depends(get_location_alert_service),  # noqa: B008
    ) -> LocationAlertResponse:
        client_host = request.client.host if request.client is not None else "unknown"
        result = await service.send(token=token, request=payload, client_key=client_host)
        return LocationAlertResponse(status=result.status)

    return router


async def get_location_alert_service(request: Request) -> LocationAlertService:
    configured: Any = getattr(request.app.state, "location_alert_service", None)
    if configured is not None:
        return configured
    lookup = await get_public_profile_lookup_service(request)
    database = getattr(request.app.state, "database", None)
    database_value = getattr(database, "database", None)
    audit_repository = None
    if database_value is not None:
        audit_repository = MongoLocationAlertAuditRepository(
            database_value,
            request.app.state.settings,
        )
    return LocationAlertService(
        lookup=lookup,
        geocoder=GoogleReverseGeocoder(request.app.state.settings),
        email_delivery=BrevoEmailDelivery(request.app.state.settings),
        audit_repository=audit_repository,
        limiter=request.app.state.location_alert_limiter,
    )


async def get_public_profile_lookup_service(request: Request) -> PublicProfileLookupService:
    """Build the anonymous lookup service over managed or injected repositories."""

    link_repository: Any = getattr(request.app.state, "public_access_link_repository", None)
    if link_repository is None:
        link_repository = PublicAccessLinkRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    profile_repository: Any = getattr(request.app.state, "profile_repository", None)
    if profile_repository is None:
        profile_repository = ProfileRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    assignment_repository: Any = getattr(request.app.state, "card_link_assignment_repository", None)
    database = getattr(request.app.state, "database", None)
    database_value = getattr(database, "database", None)
    if assignment_repository is None and database_value is not None:
        assignment_repository = CardLinkAssignmentRepository(
            database_value,
            request.app.state.settings,
        )

    return PublicProfileLookupService(
        link_repository,
        profile_repository,
        assignment_repository=assignment_repository,
        token_max_length=request.app.state.settings.emergency_token_max_length,
    )
