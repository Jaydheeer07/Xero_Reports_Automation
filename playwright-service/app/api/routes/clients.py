"""Client management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import structlog

from app.db.connection import get_db
from app.db.models import Client
from app.models.requests import ClientCreate, ClientUpdate
from app.api.dependencies import verify_api_key

router = APIRouter()
logger = structlog.get_logger()


@router.get("/")
async def list_clients(
    active_only: bool = True,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """List all clients/tenants."""
    query = select(Client)
    if active_only:
        query = query.where(Client.is_active == True)
    
    result = await db.execute(query)
    clients = result.scalars().all()
    
    return {
        "success": True,
        "count": len(clients),
        "clients": [
            {
                "id": c.id,
                "tenant_id": c.tenant_id,
                "tenant_name": c.tenant_name,
                "tenant_shortcode": c.tenant_shortcode,
                "is_active": c.is_active,
                "onedrive_folder": c.onedrive_folder,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in clients
        ]
    }


@router.get("/{client_id}")
async def get_client(
    client_id: int,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific client by ID."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    return {
        "id": client.id,
        "tenant_id": client.tenant_id,
        "tenant_name": client.tenant_name,
        "tenant_shortcode": client.tenant_shortcode,
        "is_active": client.is_active,
        "onedrive_folder": client.onedrive_folder,
        "created_at": client.created_at.isoformat() if client.created_at else None,
        "updated_at": client.updated_at.isoformat() if client.updated_at else None,
    }


@router.post("/")
async def create_client(
    request: ClientCreate,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Create a new client."""
    # Check if tenant_id already exists
    existing = await db.execute(
        select(Client).where(Client.tenant_id == request.tenant_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, 
            detail=f"Client with tenant_id '{request.tenant_id}' already exists"
        )
    
    client = Client(
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name,
        tenant_shortcode=request.tenant_shortcode,
        onedrive_folder=request.onedrive_folder,
        is_active=request.is_active
    )
    
    db.add(client)
    await db.commit()
    await db.refresh(client)
    
    logger.info("Client created", tenant_id=request.tenant_id, tenant_name=request.tenant_name)
    
    return {
        "success": True,
        "message": "Client created",
        "client": {
            "id": client.id,
            "tenant_id": client.tenant_id,
            "tenant_name": client.tenant_name,
            "tenant_shortcode": client.tenant_shortcode,
            "is_active": client.is_active,
            "onedrive_folder": client.onedrive_folder,
        }
    }


@router.put("/{client_id}")
async def update_client(
    client_id: int,
    request: ClientUpdate,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing client."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Update fields if provided
    if request.tenant_name is not None:
        client.tenant_name = request.tenant_name
    if request.tenant_shortcode is not None:
        client.tenant_shortcode = request.tenant_shortcode
    if request.onedrive_folder is not None:
        client.onedrive_folder = request.onedrive_folder
    if request.is_active is not None:
        client.is_active = request.is_active
    
    await db.commit()
    await db.refresh(client)
    
    logger.info("Client updated", client_id=client_id)
    
    return {
        "success": True,
        "message": "Client updated",
        "client": {
            "id": client.id,
            "tenant_id": client.tenant_id,
            "tenant_name": client.tenant_name,
            "tenant_shortcode": client.tenant_shortcode,
            "is_active": client.is_active,
            "onedrive_folder": client.onedrive_folder,
        }
    }


@router.delete("/{client_id}")
async def delete_client(
    client_id: int,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Delete a client (soft delete by setting is_active=False)."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    client.is_active = False
    await db.commit()
    
    logger.info("Client deactivated", client_id=client_id, tenant_id=client.tenant_id)
    
    return {
        "success": True,
        "message": "Client deactivated"
    }
