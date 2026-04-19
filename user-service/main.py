from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import sqlite3
import uuid
from datetime import datetime

app = FastAPI(title="User Service", version="1.0.0", description="Manages users for the Ticket-VCS Integration System")

DB_PATH = "/data/users.db"

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
    role: str = "developer"  # developer | manager | qa

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

@app.get("/health")
def health():
    return {"status": "healthy", "service": "user-service"}

@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(user: UserCreate):
    valid_roles = ["developer", "manager", "qa"]
    if user.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {valid_roles}")
    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
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
    return UserResponse(id=user_id, username=user.username, email=user.email,
                        full_name=user.full_name, role=user.role, created_at=now, updated_at=now)

@app.get("/users", response_model=List[UserResponse])
def list_users(role: Optional[str] = None):
    conn = get_db()
    if role:
        rows = conn.execute("SELECT * FROM users WHERE role=?", (role,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]

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
    now = datetime.utcnow().isoformat()
    fields = dict(row)
    if update.email: fields["email"] = update.email
    if update.full_name: fields["full_name"] = update.full_name
    if update.role:
        valid_roles = ["developer", "manager", "qa"]
        if update.role not in valid_roles:
            raise HTTPException(status_code=400, detail=f"Role must be one of: {valid_roles}")
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
