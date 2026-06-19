import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
import sys
import os

# append project path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2_the_architecture.middleware import get_tenant_db, init_db_client
from q4_the_judgment_call.idempotency import IdempotencyManager

# ==========================================
# Question 2 Tests: Tenant Isolation
# ==========================================

@pytest.mark.asyncio
async def test_get_tenant_db_success():
    mock_client = MagicMock()
    mock_db = MagicMock()
    mock_client.__getitem__.return_value = mock_db
    init_db_client(mock_client)

    mock_request = MagicMock()
    mock_request.headers = {"X-Tenant-ID": "tenant_123"}
    mock_request.state = MagicMock()

    db = await get_tenant_db(mock_request)

    mock_client.__getitem__.assert_called_once_with("exposcan_tenant_tenant_123")
    assert db == mock_db

@pytest.mark.asyncio
async def test_get_tenant_db_missing_header():
    mock_request = MagicMock()
    mock_request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await get_tenant_db(mock_request)
    
    assert exc_info.value.status_code == 401
    assert "missing header" in exc_info.value.detail

@pytest.mark.asyncio
async def test_get_tenant_db_invalid_format():
    mock_request = MagicMock()
    mock_request.headers = {"X-Tenant-ID": "invalid;db_name$"}

    with pytest.raises(HTTPException) as exc_info:
        await get_tenant_db(mock_request)
    
    assert exc_info.value.status_code == 400
    assert "invalid tenant" in exc_info.value.detail


# ==========================================
# Question 4 Tests: Atomic Idempotency Key Manager
# ==========================================

@pytest.mark.asyncio
async def test_idempotency_manager_lock_success():
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    mock_collection.insert_one = AsyncMock(return_value=MagicMock())

    manager = IdempotencyManager(mock_db, collection_name="test_idempotency")
    result = await manager.start_request("unique-key-111")
    
    assert result is None
    mock_collection.insert_one.assert_called_once()
    inserted_doc = mock_collection.insert_one.call_args[0][0]
    assert inserted_doc["_id"] == "unique-key-111"
    assert inserted_doc["status"] == "processing"

@pytest.mark.asyncio
async def test_idempotency_manager_cached_response():
    from pymongo.errors import DuplicateKeyError
    
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    
    error_response = {"ok": 0.0, "errmsg": "E11000 duplicate key error"}
    mock_collection.insert_one = MagicMock()
    mock_collection.insert_one.side_effect = DuplicateKeyError("duplicate key", error_response)
    
    cached_record = {
        "_id": "existing-key-222",
        "status": "completed",
        "response_body": {"data": "success_payload"},
        "response_status_code": 200
    }
    mock_collection.find_one = AsyncMock(return_value=cached_record)

    manager = IdempotencyManager(mock_db, collection_name="test_idempotency")
    result = await manager.start_request("existing-key-222")
    
    assert result is not None
    assert result["status_code"] == 200
    assert result["body"] == {"data": "success_payload"}
    mock_collection.find_one.assert_called_once_with({"_id": "existing-key-222"})

@pytest.mark.asyncio
async def test_idempotency_manager_concurrency_lock():
    from pymongo.errors import DuplicateKeyError
    
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    
    error_response = {"ok": 0.0, "errmsg": "E11000 duplicate key error"}
    mock_collection.insert_one = MagicMock()
    mock_collection.insert_one.side_effect = DuplicateKeyError("duplicate key", error_response)
    
    processing_record = {
        "_id": "con-key-333",
        "status": "processing",
        "response_body": None,
        "response_status_code": None
    }
    mock_collection.find_one = AsyncMock(return_value=processing_record)

    manager = IdempotencyManager(mock_db, collection_name="test_idempotency")
    
    with pytest.raises(HTTPException) as exc_info:
        await manager.start_request("con-key-333")
        
    assert exc_info.value.status_code == 425
    assert "duplicate request" in exc_info.value.detail
