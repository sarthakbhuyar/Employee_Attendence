# Employee Attendance & Analytics API 🕒

A FastAPI and MongoDB application for managing employee records, daily attendance, manual corrections, and workforce analytics. It includes a responsive browser dashboard and an OpenAPI contract.

## ✨ Features

- Create and browse employees, with unique employee codes.
- Record punch-ins and punch-outs, including work-from-home and on-duty attendance.
- Correct attendance records with a reason and an append-only audit history.
- Calculate late minutes, work hours, overtime, and half-day status.
- View monthly employee metrics, department summaries, late-arrival rankings, and department trends.
- Inspect query plans for supported reporting operations.
- Use the dashboard at `/` or the interactive API reference at `/docs`.

## 🧰 Requirements

- Python 3.11 or newer.
- MongoDB 6.0 or newer (MongoDB 7.0 is supported).
- `pip` to install Python dependencies.

MongoDB can run locally or be hosted by MongoDB Atlas. The application defaults to `mongodb://localhost:27017` and database `attendance_db` if the environment variables below are not set.

## 🚀 Run locally (without a virtual environment)

The steps below install the Python packages into your current Python installation. A virtual environment is optional; you do not need one to run this project.

### 1. Install prerequisites

Install **Python 3.11+** and **Git**. Confirm Python and pip are available:

```text
python --version
python -m pip --version
```

On Windows, use `py` instead of `python` if that is how your Python installation is configured.

You also need MongoDB 6.0 or newer. Choose one of these options:

- Install and start MongoDB Community Server on your computer.
- Use a MongoDB Atlas database and copy its connection string.
- If Docker is installed, start MongoDB 7 with `docker run --name attendance-mongo -p 27017:27017 -d mongo:7`.

### 2. Download the project from GitHub

Replace `<your-github-username>` with the account that hosts this repository, or copy the clone URL from the repository's **Code** button:

```bash
git clone https://github.com/<your-github-username>/candidate_kit.git
cd candidate_kit
```

### 3. Install Python dependencies (no venv)

Run these commands from the project folder:

**Windows PowerShell:**

```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

If your system Python refuses a global install (some Linux distributions mark it as externally managed), use the optional virtual-environment commands in [Optional: use a virtual environment](#optional-use-a-virtual-environment).

### 4. Configure MongoDB

The app defaults to `mongodb://localhost:27017` and database `attendance_db`, so no configuration file is needed for a default local MongoDB installation. To choose another connection, create a file named `.env` in the project root with:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

For Atlas, set `MONGO_URI` to the connection string from Atlas and `MONGO_DB` to your database name. Ensure your Atlas network access settings permit your computer to connect. Keep credentials private: never commit `.env` or publish database secrets. This repository does not include an `.env.example` file.

### 5. (Optional) Load sample records

**Windows PowerShell:**

```powershell
py sample_seed.py
```

**macOS / Linux:**

```bash
python3 sample_seed.py
```

⚠️ **Seeding replaces all data in the configured database's `employees` and `attendance_logs` collections.** The script deletes those collections' documents before inserting samples. Use a disposable development database and do not seed a database containing data you need. The samples show document shapes only and do not cover every business-rule edge case.

### 6. Start the application

**Windows PowerShell:**

```powershell
py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**macOS / Linux:**

```bash
python3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open these pages in a browser:

- Dashboard: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Readiness check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

The `--reload` option is useful during development and can be omitted for a standard run. The service creates its MongoDB indexes at startup. `/health` returns success only when MongoDB responds to a ping. Stop the server with `Ctrl+C`.

### Optional: use a virtual environment

A virtual environment keeps these packages separate from other Python projects. It is optional; use these commands instead of the dependency-install step above if you prefer one.

**Windows PowerShell:**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 📡 API overview

The complete request and response schemas, validation rules, status codes, and business rules are in [`openapi.yaml`](openapi.yaml). FastAPI also serves the same contract through `/docs` and `/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Check application and MongoDB readiness. |
| `POST` | `/employees` | Create an employee. |
| `GET` | `/employees` | List employees, with optional department filter and pagination. |
| `POST` | `/attendance/punch-in` | Record a punch-in. |
| `POST` | `/attendance/punch-out` | Record a punch-out. |
| `GET` | `/attendance` | List attendance, with filters and pagination. |
| `PATCH` | `/attendance/{emp_code}/{day}` | Correct a record and append an audit-history entry. |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Get an employee's monthly metrics. |
| `GET` | `/analytics/departments/summary` | Get monthly department summaries. |
| `GET` | `/analytics/leaderboard/late` | Rank employees by late minutes for a month. |
| `GET` | `/analytics/departments/{department}/trend` | Get a department's daily trend over a date range. |
| `GET` | `/admin/explain/{endpoint}` | Inspect the query plan for a supported operation. |

### Example requests

Create an employee (the code is supplied by the client):

```bash
curl -X POST http://127.0.0.1:8000/employees \
  -H "Content-Type: application/json" \
  -d '{"emp_code":"EMP0001","name":"Asha Rao","email":"asha@example.com","department":"Engineering","shift_start":"09:30","shift_end":"18:30","joined_on":"2026-01-05"}'
```

Record a punch-in. `punched_at` is optional; when omitted, the server uses its current time. If supplied, it must be epoch **milliseconds**:

```bash
curl -X POST http://127.0.0.1:8000/attendance/punch-in \
  -H "Content-Type: application/json" \
  -d '{"emp_code":"EMP0001","status":"PRESENT"}'
```

Request monthly department analytics:

```bash
curl "http://127.0.0.1:8000/analytics/departments/summary?month=2026-01"
```

## 🧭 Data and time rules

- Employees are identified by `emp_code`; MongoDB `_id` is internal and is not exposed by the API.
- MongoDB stores instants as UTC BSON dates. The HTTP API accepts and returns epoch milliseconds for instants such as punches and `created_at`.
- Attendance dates are `YYYY-MM-DD` calendar dates in India Standard Time (IST); shift times use 24-hour `HH:MM` IST.
- Overnight shifts assign early-morning punches before the shift end to the previous shift-start date.
- A punch-in is late only when it is strictly more than 10 minutes after shift start. Overtime is counted when it reaches at least 30 minutes after shift end.
- `PRESENT`, `WFH`, and `ON_DUTY` count as present. `ABSENT` and `LEAVE` do not.
- Manual corrections require a reason and create an audit-history entry. Punch-in and punch-out do not add correction-history entries.
- Validation errors use HTTP `422`; duplicate employee codes or duplicate daily punch-ins use `409`; unknown employees use `404` where applicable.

See [`openapi.yaml`](openapi.yaml) for the full rules (R1–R10) and [`DATA_MODEL.md`](DATA_MODEL.md) for MongoDB collection fields and sample document shapes.

## 🗂️ Project layout

```text
.
├── app/
│   ├── main.py              # FastAPI routes, validation, calculations, and MongoDB access
│   └── static/index.html    # Responsive dashboard served by FastAPI
├── sample_data/             # Sample employee and attendance documents
├── DATA_MODEL.md             # MongoDB schema and sample data notes
├── DECISIONS.md              # Design decisions and trade-offs
├── openapi.yaml              # API contract and business rules
├── requirements.txt          # Python dependencies
├── REVIEW.md                 # Starter-code review and fixes
└── sample_seed.py            # Optional sample-data loader (clears target collections)
```

## 🧪 Development notes

- Start the service with `uvicorn app.main:app --reload --port 8000` while developing.
- `sample_seed.py` reads `MONGO_URI` and `MONGO_DB` from environment variables or `.env`.
- Database indexes are created by the application when it starts; the attendance uniqueness constraint prevents more than one record per employee per attendance day.
- The dashboard uses the same-origin API served by FastAPI; no separate frontend build step is required.

## 📚 Additional documentation

- [API contract and business rules](openapi.yaml)
- [MongoDB data model](DATA_MODEL.md)
- [Implementation decisions](DECISIONS.md)
- [Review notes](REVIEW.md)
