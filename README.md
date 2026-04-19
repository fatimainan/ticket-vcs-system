# 🎫 Ticket Management & Version Control Integration System
> **Group 10** — Microservices Architecture Project

---

## Architecture Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  User Service   │    │ Ticket Service  │    │  VCS Service    │
│   Port: 8001    │◄───│   Port: 8002    │◄───│   Port: 8003    │
│                 │    │                 │    │                 │
│  Manage users   │    │ Manage tickets  │    │ Git-like commits │
│  roles: dev,    │    │ OPEN→IN_PROGRESS│    │ commit_id (SHA1) │
│  manager, qa    │    │ →IN_REVIEW→DONE │    │ linked ticket_id │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Core Business Rule
> **A ticket CANNOT be marked as `DONE` unless at least one commit is linked to it.**
> **Every commit MUST reference a valid Ticket ID.**

---

## Quick Start

```bash
git clone <repo-url>
cd ticket-vcs-system
docker compose up --build
```

Services will be available at:
- User Service:   http://localhost:8001/docs
- Ticket Service: http://localhost:8002/docs
- VCS Service:    http://localhost:8003/docs

---

## API Reference

### User Service (port 8001)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /users | Create user |
| GET | /users | List all users |
| GET | /users/{id} | Get user by ID |
| GET | /users/by-username/{username} | Get user by username |
| PUT | /users/{id} | Update user |
| DELETE | /users/{id} | Delete user |

**Create User:**
```json
POST /users
{
  "username": "john_dev",
  "email": "john@example.com",
  "full_name": "John Developer",
  "role": "developer"
}
```

### Ticket Service (port 8002)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /tickets | Create ticket |
| GET | /tickets | List tickets (filter by status/project/assignee) |
| GET | /tickets/{id} | Get ticket |
| PUT | /tickets/{id} | Update ticket (enforces DONE rule) |
| DELETE | /tickets/{id} | Delete ticket |
| GET | /tickets/{id}/commits | Get all commits for ticket |
| GET | /tickets/{id}/summary | Full ticket + commits + user info |
| GET | /projects/{project}/stats | Project statistics |

**Create Ticket:**
```json
POST /tickets
{
  "title": "Fix login bug",
  "description": "Users cannot login with SSO",
  "priority": "HIGH",
  "assignee_id": "<user-id>",
  "project": "AUTH-PROJECT"
}
```

**Try to mark DONE without commit (will fail):**
```json
PUT /tickets/TICK-XXXXXXXX
{ "status": "DONE" }

→ 422 Unprocessable Entity
{
  "error": "DONE_REQUIRES_COMMIT",
  "message": "Ticket cannot be marked as DONE without at least one linked commit."
}
```

### VCS Service (port 8003)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /commits | Create commit (ticket_id required!) |
| GET | /commits | List commits |
| GET | /commits/{id} | Get commit by full/short hash |
| GET | /commits/{id}/ticket | Get which ticket this commit belongs to |
| GET | /commits/by-ticket/{ticket_id} | Get all commits for a ticket |
| POST | /branches | Create branch |
| GET | /branches | List branches |
| GET | /repositories/{repo}/stats | Repository statistics |

**Create Commit:**
```json
POST /commits
{
  "message": "fix: resolve SSO token expiry issue",
  "ticket_id": "TICK-XXXXXXXX",
  "author_id": "<user-id>",
  "branch": "feature/fix-sso",
  "repository": "auth-service",
  "files_changed": 3,
  "additions": 45,
  "deletions": 12
}
```

---

## Full Workflow Example

```bash
# 1. Create a user
curl -X POST http://localhost:8001/users \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@co.com","full_name":"Alice Smith","role":"developer"}'

# 2. Create a ticket
curl -X POST http://localhost:8002/tickets \
  -H "Content-Type: application/json" \
  -d '{"title":"Add dark mode","priority":"MEDIUM","project":"UI"}'

# 3. Try to close it — FAILS
curl -X PUT http://localhost:8002/tickets/TICK-XXXXXXXX \
  -H "Content-Type: application/json" \
  -d '{"status":"DONE"}'

# 4. Create a commit linked to the ticket
curl -X POST http://localhost:8003/commits \
  -H "Content-Type: application/json" \
  -d '{"message":"feat: implement dark mode toggle","ticket_id":"TICK-XXXXXXXX","author_id":"<user-id>"}'

# 5. Now close the ticket — SUCCEEDS
curl -X PUT http://localhost:8002/tickets/TICK-XXXXXXXX \
  -H "Content-Type: application/json" \
  -d '{"status":"DONE"}'
```
