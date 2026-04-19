from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import uuid
from datetime import datetime
import httpx
import os

app = FastAPI(
    title="Ticket Service",
    version="1.0.0",
    description="Manages tickets with enforced Version Control integration (DONE requires a linked commit)"
)

DB_PATH = "/data/tickets.db"
VCS_SERVICE_URL = os.getenv("VCS_SERVICE_URL", "http://vcs-service:8003")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8001")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs("/data", exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            priority TEXT NOT NULL DEFAULT 'MEDIUM',
            assignee_id TEXT,
            reporter_id TEXT,
            project TEXT NOT NULL DEFAULT 'DEFAULT',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()


class TicketCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "MEDIUM"      # LOW | MEDIUM | HIGH | CRITICAL
    assignee_id: Optional[str] = None
    reporter_id: Optional[str] = None
    project: str = "DEFAULT"


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # OPEN | IN_PROGRESS | IN_REVIEW | DONE
    priority: Optional[str] = None
    assignee_id: Optional[str] = None
    project: Optional[str] = None


class TicketResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    status: str
    priority: str
    assignee_id: Optional[str]
    reporter_id: Optional[str]
    project: str
    created_at: str
    updated_at: str


VALID_STATUSES = ["OPEN", "IN_PROGRESS", "IN_REVIEW", "DONE"]
VALID_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@app.get("/health")
def health():
    return {"status": "healthy", "service": "ticket-service"}


@app.post("/tickets", response_model=TicketResponse, status_code=201)
def create_ticket(ticket: TicketCreate):
    if ticket.priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"Priority must be one of: {VALID_PRIORITIES}")

    ticket_id = f"TICK-{str(uuid.uuid4())[:8].upper()}"
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO tickets
           (id, title, description, status, priority, assignee_id, reporter_id, project, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ticket_id, ticket.title, ticket.description, "OPEN", ticket.priority,
         ticket.assignee_id, ticket.reporter_id, ticket.project, now, now)
    )
    conn.commit()
    conn.close()
    return TicketResponse(
        id=ticket_id, title=ticket.title, description=ticket.description,
        status="OPEN", priority=ticket.priority, assignee_id=ticket.assignee_id,
        reporter_id=ticket.reporter_id, project=ticket.project,
        created_at=now, updated_at=now
    )


@app.get("/tickets", response_model=List[TicketResponse])
def list_tickets(status: Optional[str] = None, project: Optional[str] = None,
                 assignee_id: Optional[str] = None):
    conn = get_db()
    query = "SELECT * FROM tickets WHERE 1=1"
    params = []
    if status:
        query += " AND status=?"
        params.append(status)
    if project:
        query += " AND project=?"
        params.append(project)
    if assignee_id:
        query += " AND assignee_id=?"
        params.append(assignee_id)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    return dict(row)


@app.put("/tickets/{ticket_id}", response_model=TicketResponse)
def update_ticket(ticket_id: str, update: TicketUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")

    fields = dict(row)
    now = datetime.utcnow().isoformat()

    # ─── CORE BUSINESS RULE: Cannot mark DONE without a linked commit ───
    if update.status == "DONE":
        try:
            response = httpx.get(f"{VCS_SERVICE_URL}/commits/by-ticket/{ticket_id}", timeout=5.0)
            if response.status_code == 200:
                commits = response.json()
                if len(commits) == 0:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": "DONE_REQUIRES_COMMIT",
                            "message": f"Ticket '{ticket_id}' cannot be marked as DONE without at least one linked commit. "
                                       "Please commit your code and link it to this ticket first.",
                            "ticket_id": ticket_id,
                            "linked_commits": 0
                        }
                    )
            else:
                raise HTTPException(status_code=502, detail="Could not verify commits from VCS service")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="VCS Service is unreachable. Cannot validate commit requirement.")

    if update.title: fields["title"] = update.title
    if update.description: fields["description"] = update.description
    if update.status:
        if update.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Status must be one of: {VALID_STATUSES}")
        fields["status"] = update.status
    if update.priority:
        if update.priority not in VALID_PRIORITIES:
            raise HTTPException(status_code=400, detail=f"Priority must be one of: {VALID_PRIORITIES}")
        fields["priority"] = update.priority
    if update.assignee_id is not None: fields["assignee_id"] = update.assignee_id
    if update.project: fields["project"] = update.project
    fields["updated_at"] = now

    conn.execute(
        "UPDATE tickets SET title=?, description=?, status=?, priority=?, assignee_id=?, project=?, updated_at=? WHERE id=?",
        (fields["title"], fields["description"], fields["status"], fields["priority"],
         fields["assignee_id"], fields["project"], now, ticket_id)
    )
    conn.commit()
    conn.close()
    return fields


@app.delete("/tickets/{ticket_id}", status_code=204)
def delete_ticket(ticket_id: str):
    conn = get_db()
    result = conn.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")


@app.get("/tickets/{ticket_id}/commits")
def get_ticket_commits(ticket_id: str):
    """Get all commits linked to this ticket (proxied from VCS Service)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    try:
        response = httpx.get(f"{VCS_SERVICE_URL}/commits/by-ticket/{ticket_id}", timeout=5.0)
        commits = response.json()
        return {
            "ticket_id": ticket_id,
            "ticket_title": row["title"],
            "ticket_status": row["status"],
            "commit_count": len(commits),
            "commits": commits
        }
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="VCS Service is unreachable")


@app.get("/tickets/{ticket_id}/summary")
def get_ticket_summary(ticket_id: str):
    """Full ticket summary including linked commits and user info."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")

    ticket_data = dict(row)

    # Fetch commits
    try:
        r = httpx.get(f"{VCS_SERVICE_URL}/commits/by-ticket/{ticket_id}", timeout=5.0)
        commits = r.json() if r.status_code == 200 else []
    except Exception:
        commits = []

    # Fetch assignee info
    assignee = None
    if ticket_data.get("assignee_id"):
        try:
            r = httpx.get(f"{USER_SERVICE_URL}/users/{ticket_data['assignee_id']}", timeout=5.0)
            if r.status_code == 200:
                assignee = r.json()
        except Exception:
            pass

    return {
        "ticket": ticket_data,
        "assignee": assignee,
        "commits": commits,
        "commit_count": len(commits),
        "can_be_closed": len(commits) > 0
    }


@app.get("/projects/{project}/stats")
def get_project_stats(project: str):
    """Return ticket statistics per project."""
    conn = get_db()
    rows = conn.execute("SELECT status, COUNT(*) as count FROM tickets WHERE project=? GROUP BY status", (project,)).fetchall()
    conn.close()
    stats = {s: 0 for s in VALID_STATUSES}
    for row in rows:
        stats[row["status"]] = row["count"]
    return {"project": project, "stats": stats, "total": sum(stats.values())}
