# Context: Addendum & Glue Logic

### 1. Missing Data Model Field (Agent Service)
**Critical**: You must update the Pydantic model in the Agent Service, otherwise `main.py` will throw a validation error when the Kernel tries to pass the `run_id`.

**File:** `apps/agent-service/src/domain/protocol.py`

```python
class AgentRequest(BaseModel):
    """Request from the Kernel to execute a specific agent node."""
    # ... existing fields ...
    agent_id: str
    model: str
    prompt: str
    input_data: Dict[str, Any]
    
    # === NEW FIELD ===
    # Required for RFS WorkspaceManager to locate the session folder
    run_id: str 
    
    # ... existing optional fields ...
    tools: List[str] = []
    thought_signature: Optional[str] = None
    # ...
```

### 2. Operational Safety (Git & Docker)
To prevent committing user data and ensure Docker permissions work:

**File:** `.gitignore` (Root)
```text
# RARO File System
storage/library/*
!storage/library/.keep
storage/sessions/*
!storage/sessions/.keep
```

**Note on Docker Permissions:**
Ensure the `storage/` directory on the host machine has read/write permissions for the user ID running inside the container (usually `1000:1000` for non-root Node/Python images, or root).
*Command:* `chmod -R 777 ./storage` (For local dev).