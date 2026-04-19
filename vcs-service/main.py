from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import uuid
import hashlib
from datetime import datetime
import httpx
import os

app = FastAPI(
    title="Version Control Service",
    version="1.0.0",
    description="Simulates Git-like commits. Every commit MUST be linked to a Ticket ID."
)

DB_PATH = "/data/vcs.db"
TICKET_SERVICE_URL = os.getenv("TICKET_SERVICE_URL", "http://ticket-service:8002")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8001")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def generate_commit_hash(message: str, author_id: str, timestamp: str) -> str:
    """Generate a realistic git-like 40-char SHA1 commit hash."""
    content = f"{message}{author_id}{timestamp}{uuid.uuid4()}"
    return hashlib.sha1(content.encode()).hexdigest()


def init_db():
    os.makedirs("/data", exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS commits (
            commit_id TEXT PRIMARY KEY,
            short_hash TEXT NOT NULL,
            message TEXT NOT NULL,
            ticket_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT 'main',
            repository TEXT NOT NULL DEFAULT 'default-repo',
            files_changed INTEGER DEFAULT 0,
            additions INTEGER DEFAULT 0,
            deletions INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            repository TEXT NOT NULL,
            created_from TEXT DEFAULT 'main',
            author_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(name, repository)
        )
    """)
    conn.commit()
    conn.close()


init_db()


class CommitCreate(BaseModel):
    message: str
    ticket_id: str                       # MANDATORY – every commit needs a ticket
    author_id: str
    branch: str = "main"
    repository: str = "default-repo"
    files_changed: int = 1
    additions: int = 0
    deletions: int = 0


class CommitResponse(BaseModel):
    commit_id: str
    short_hash: str
    message: str
    ticket_id: str
    author_id: str
    branch: str
    repository: str
    files_changed: int
    additions: int
    deletions: int
    timestamp: str


class BranchCreate(BaseModel):
    name: str
    repository: str = "default-repo"
    created_from: str = "main"
    author_id: str


@app.get("/health")
def health():
    return {"status": "healthy", "service": "vcs-service"}


@app.post("/commits", response_model=CommitResponse, status_code=201)
def create_commit(commit: CommitCreate):
    """
    Create a new commit. ticket_id is REQUIRED.
    The referenced ticket must exist in the Ticket Service.
    """
    if not commit.ticket_id or not commit.ticket_id.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "TICKET_ID_REQUIRED",
                "message": "Every commit must reference a valid Ticket ID. "
                           "Please provide ticket_id in your request."
            }
        )

    if not commit.message or len(commit.message.strip()) < 5:
        raise HTTPException(status_code=400, detail="Commit message must be at least 5 characters.")

    # Validate ticket exists in Ticket Service
    try:
        r = httpx.get(f"{TICKET_SERVICE_URL}/tickets/{commit.ticket_id}", timeout=5.0)
        if r.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "TICKET_NOT_FOUND",
                    "message": f"Ticket '{commit.ticket_id}' does not exist. Cannot create a commit for a non-existent ticket.",
                    "ticket_id": commit.ticket_id
                }
            )
        elif r.status_code != 200:
            raise HTTPException(status_code=502, detail="Unexpected response from Ticket Service")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Ticket Service is unreachable. Cannot validate ticket.")

    now = datetime.utcnow().isoformat()
    full_hash = generate_commit_hash(commit.message, commit.author_id, now)
    short_hash = full_hash[:7]

    conn = get_db()
    conn.execute(
        """INSERT INTO commits
           (commit_id, short_hash, message, ticket_id, author_id, branch, repository,
            files_changed, additions, deletions, timestamp)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (full_hash, short_hash, commit.message, commit.ticket_id, commit.author_id,
         commit.branch, commit.repository, commit.files_changed,
         commit.additions, commit.deletions, now)
    )
    conn.commit()
    conn.close()

    return CommitResponse(
        commit_id=full_hash, short_hash=short_hash, message=commit.message,
        ticket_id=commit.ticket_id, author_id=commit.author_id,
        branch=commit.branch, repository=commit.repository,
        files_changed=commit.files_changed, additions=commit.additions,
        deletions=commit.deletions, timestamp=now
    )


@app.get("/commits", response_model=List[CommitResponse])
def list_commits(repository: Optional[str] = None, branch: Optional[str] = None,
                 author_id: Optional[str] = None, limit: int = 50):
    conn = get_db()
    query = "SELECT * FROM commits WHERE 1=1"
    params = []
    if repository:
        query += " AND repository=?"
        params.append(repository)
    if branch:
        query += " AND branch=?"
        params.append(branch)
    if author_id:
        query += " AND author_id=?"
        params.append(author_id)
    query += f" ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/commits/by-ticket/{ticket_id}", response_model=List[CommitResponse])
def get_commits_by_ticket(ticket_id: str):
    """Get all commits linked to a specific ticket. This is the CORE integration endpoint."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM commits WHERE ticket_id=? ORDER BY timestamp DESC", (ticket_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/commits/{commit_id}", response_model=CommitResponse)
def get_commit(commit_id: str):
    conn = get_db()
    # Support both full hash and short hash lookup
    row = conn.execute(
        "SELECT * FROM commits WHERE commit_id=? OR short_hash=?", (commit_id, commit_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Commit '{commit_id}' not found")
    return dict(row)


@app.get("/commits/{commit_id}/ticket")
def get_commit_ticket(commit_id: str):
    """Find which ticket this commit belongs to."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM commits WHERE commit_id=? OR short_hash=?", (commit_id, commit_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Commit '{commit_id}' not found")
    commit_data = dict(row)
    ticket_id = commit_data["ticket_id"]

    try:
        r = httpx.get(f"{TICKET_SERVICE_URL}/tickets/{ticket_id}", timeout=5.0)
        if r.status_code == 200:
            return {
                "commit_id": commit_data["commit_id"],
                "short_hash": commit_data["short_hash"],
                "commit_message": commit_data["message"],
                "commit_timestamp": commit_data["timestamp"],
                "ticket": r.json()
            }
    except Exception:
        pass

    return {
        "commit_id": commit_data["commit_id"],
        "short_hash": commit_data["short_hash"],
        "commit_message": commit_data["message"],
        "commit_timestamp": commit_data["timestamp"],
        "ticket_id": ticket_id,
        "ticket": None,
        "note": "Could not fetch ticket details"
    }


# ─── Branch Management ────────────────────────────────────────────────────────

@app.post("/branches", status_code=201)
def create_branch(branch: BranchCreate):
    branch_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO branches (id, name, repository, created_from, author_id, created_at) VALUES (?,?,?,?,?,?)",
            (branch_id, branch.name, branch.repository, branch.created_from, branch.author_id, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Branch '{branch.name}' already exists in '{branch.repository}'")
    finally:
        conn.close()
    return {"id": branch_id, "name": branch.name, "repository": branch.repository,
            "created_from": branch.created_from, "author_id": branch.author_id, "created_at": now}


@app.get("/branches")
def list_branches(repository: Optional[str] = None):
    conn = get_db()
    if repository:
        rows = conn.execute("SELECT * FROM branches WHERE repository=?", (repository,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM branches").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Repository Stats ─────────────────────────────────────────────────────────

@app.get("/repositories/{repository}/stats")
def get_repo_stats(repository: str):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM commits WHERE repository=?", (repository,)).fetchone()[0]
    tickets = conn.execute(
        "SELECT COUNT(DISTINCT ticket_id) FROM commits WHERE repository=?", (repository,)
    ).fetchone()[0]
    authors = conn.execute(
        "SELECT COUNT(DISTINCT author_id) FROM commits WHERE repository=?", (repository,)
    ).fetchone()[0]
    branches_count = conn.execute(
        "SELECT COUNT(*) FROM branches WHERE repository=?", (repository,)
    ).fetchone()[0]
    recent = conn.execute(
        "SELECT * FROM commits WHERE repository=? ORDER BY timestamp DESC LIMIT 5", (repository,)
    ).fetchall()
    conn.close()
    return {
        "repository": repository,
        "total_commits": total,
        "tickets_referenced": tickets,
        "unique_authors": authors,
        "branch_count": branches_count,
        "recent_commits": [dict(r) for r in recent]
    }
