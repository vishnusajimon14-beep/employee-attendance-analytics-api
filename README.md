# Employee Attendance & Analytics API

A FastAPI and MongoDB application for employee management, attendance tracking, attendance regularization, and analytics.

## Requirements

- Python 3.10+
- MongoDB running locally or a reachable MongoDB instance
- Dependencies listed in `requirements.txt`

## Setup

Install dependencies from the project root:

```bash
pip install -r requirements.txt
```

Configure `MONGO_URI` and `MONGO_DB` as environment variables if required. The application uses the defaults defined in `app/main.py` when they are not set.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.
## Run the API

From the project root, run:

```bash
uvicorn app.main:app --port 8000
```

Then open:

- Swagger UI: http://127.0.0.1:8000/docs

## Main Features

- Create and list employees with pagination and department filtering
- Record attendance punch-ins and punch-outs
- List attendance records and regularize attendance
- Calculate monthly employee attendance analytics
- Generate department summaries and date-wise department trends
- View the late-arrival leaderboard
- Inspect query execution plans through admin explain endpoints

## Monthly Employee Analytics

Use the monthly analytics endpoint with an employee code and month in `YYYY-MM` format:

```http
GET /analytics/employees/{emp_code}/monthly?month=2026-10
```

Replace `{emp_code}` with the required employee code and `2026-10` with the target month. The employee code must correspond to an employee in the database.

## Database Indexes

Required indexes are created automatically when the application starts. No manual index creation is required.

## Project Files

- `app/main.py` — application code and API endpoints
- `requirements.txt` — Python dependencies
- `REVIEW.md` — review of defects in the supplied starter implementation
- `DECISIONS.md` — implementation and design decisions
