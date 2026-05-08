from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import sqlite3
import uuid
from datetime import datetime, timezone
import httpx
import os

app = FastAPI(
    title="User Service",
    version="1.1.0",
    description="Manages users for the Ticket-VCS Integration System"
)

DB_PATH = "/data/users.db"
TICKET_SERVICE_URL = os.getenv("TICKET_SERVICE_URL", "http://ticket-service:8002")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    import os
    os.makedirs("/data", exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'developer',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()


class UserCreate(BaseModel):
    username: str
    email: str
    full_name: str
    role: str = "developer"

class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: str
    role: str
    created_at: str
    updated_at: str


VALID_ROLES = ["developer", "manager", "qa", "admin"]


@app.get("/health")
def health():
    return {"status": "healthy", "service": "user-service", "version": "1.1.0"}


@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(user: UserCreate):
    if user.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {VALID_ROLES}")
    
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (id, username, email, full_name, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, user.username, user.email, user.full_name, user.role, now, now)
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail=f"Username or email already exists: {str(e)}")
    finally:
        conn.close()
    return UserResponse(
        id=user_id, username=user.username, email=user.email,
        full_name=user.full_name, role=user.role, created_at=now, updated_at=now
    )


@app.get("/users", response_model=List[UserResponse])
def list_users(
    role: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    conn = get_db()
    if role:
        rows = conn.execute(
            "SELECT * FROM users WHERE role=? LIMIT ? OFFSET ?",
            (role, limit, skip)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM users LIMIT ? OFFSET ?",
            (limit, skip)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/users/count")
def get_user_count(role: Optional[str] = None):
    conn = get_db()
    if role:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role=?", (role,)).fetchone()[0]
    else:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return {"total_users": count, "role_filter": role}


@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.get("/users/by-username/{username}", response_model=UserResponse)
def get_user_by_username(username: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.put("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: str, update: UserUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    now = datetime.now(timezone.utc).isoformat()
    fields = dict(row)
    
    if update.email: fields["email"] = update.email
    if update.full_name: fields["full_name"] = update.full_name
    if update.role:
        if update.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"Role must be one of: {VALID_ROLES}")
        fields["role"] = update.role
    fields["updated_at"] = now
    
    conn.execute(
        "UPDATE users SET email=?, full_name=?, role=?, updated_at=? WHERE id=?",
        (fields["email"], fields["full_name"], fields["role"], now, user_id)
    )
    conn.commit()
    conn.close()
    return fields


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str):
    conn = get_db()
    result = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")


@app.get("/users/{user_id}/tickets")
def get_user_tickets(user_id: str, status: Optional[str] = None):
    """Fetch tickets assigned to this user from Ticket Service."""
    # First verify user exists
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        url = f"{TICKET_SERVICE_URL}/tickets?assignee_id={user_id}"
        if status:
            url += f"&status={status}"
        r = httpx.get(url, timeout=5.0)
        if r.status_code == 200:
            return {
                "user_id": user_id,
                "username": row["username"],
                "tickets": r.json()
            }
        else:
            raise HTTPException(status_code=502, detail="Could not fetch tickets from Ticket Service")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Ticket Service is unreachable")