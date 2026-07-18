"""
Phone Numbers Router
Manage Twilio phone numbers registered with Retell AI.
"""
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class ImportPhoneNumberRequest(BaseModel):
    phone_number: str           # E.164 format, e.g. +12025551234
    termination_uri: str        # Twilio SIP termination URI
    label: str = ""


@router.get("/", summary="List Phone Numbers in Retell")
async def list_phone_numbers(request: Request):
    """List all phone numbers registered in Retell AI."""
    retell = request.app.state.retell
    return await retell.list_phone_numbers()


@router.post("/import", summary="Import Twilio Number into Retell")
async def import_phone_number(request: Request, body: ImportPhoneNumberRequest):
    """
    Register a Twilio phone number with Retell AI.
    Required once per number to enable outbound calling.
    """
    retell = request.app.state.retell
    return await retell.import_phone_number(
        phone_number=body.phone_number,
        termination_uri=body.termination_uri,
        label=body.label,
    )


@router.get("/agent", summary="Get Retell Agent Config")
async def get_agent_config(request: Request):
    """Get the current Retell AI agent configuration."""
    retell = request.app.state.retell
    return await retell.get_agent()


@router.patch("/agent", summary="Update Retell Agent Config")
async def update_agent_config(request: Request, updates: dict):
    """
    Update the Retell AI agent configuration.
    Used by Agent B to push prompt improvements.
    """
    retell = request.app.state.retell
    return await retell.update_agent(updates)
