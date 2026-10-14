"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Query,Path
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError,PyMongoError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

#index
@app.on_event("startup")
def create_indexes():
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="unique_emp_code_date"
    )
    db.employees.create_index(
        [("emp_code",1)],unique=True,
        name="unique_emp_code"
    )

    db.attendance_logs.create_index(
        [("date", 1), ("late_minutes", 1)],
        name="date_late_minutes"
    )

    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="department_joined_on"
    )

    db.employees.create_index(
        [("joined_on", 1)],
        name="joined_on"
    )
    db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)],
    name="attendance_date_emp_code")



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end:str ) -> int:
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))
    start_minutes=start_h*60+start_m
    end_minutes=end_h*60+end_m
    shift_start_time = punch_in.replace(hour=start_h, minute=start_m, second=0, microsecond=0)

    if end_minutes<=start_minutes:
        punch_minutes=punch_in.hour*60+punch_in.minute
        if punch_minutes<end_minutes:
            shift_start_time-=timedelta(days=1)
    
    elapsed_second=(punch_in-shift_start_time).total_seconds()
    if elapsed_second <=10*60:
        return 0
    return int(elapsed_second //60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    # return round((punch_out - punch_in).total_seconds() / 3600, 2)
    seconds=Decimal(str((punch_out-punch_in).total_seconds()))
    hours=seconds/Decimal("3600")
    return float(hours.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_start:str, shift_end: str, date_str: str) -> int:
    end_h, end_m = map(int, shift_end.split(":"))
    start_h, start_m = map(int, shift_start.split(":"))
    start_minutes=start_h * 60 + start_m
    end_minutes=end_h * 60 + end_m
    end = datetime.fromisoformat(date_str).replace(hour=end_h, minute=end_m,second=0,microsecond=0 ,tzinfo=IST)
    #an overmight shift end on the following calender day
    if end_minutes<=start_minutes:
        end+=timedelta(days=1)  
    overtime_minutes=int((punch_out-end).total_seconds()//60)
    return overtime_minutes if overtime_minutes>=30 else 0

def datetime_to_ms(value):
    if value is None:
        return None

    if isinstance(value,(int,float)):
        return int(value)

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp() * 1000)
# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: date



class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None,
    strict=True,
    ge=100_000_000_000,
    le=4_102_444_800_000)
    # status: str = "PRESENT"
    status:Literal["PRESENT","WFH","ON_DUTY"]="PRESENT"

class PunchOutIn(BaseModel):
    emp_code:str
    punched_at:Optional[int]=Field(default=None,
    strict=True,
    ge=100_000_000_000,
    le=4_102_444_800_000)

class RegularizeRequest(BaseModel):
    status: Optional[
        Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
    ] = None

    punch_in: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )

    punch_out: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    
# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(status_code=503,detail="mongoDb is unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump(mode="json")
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = datetime_to_ms(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1,ge=1), page_size: int =Query(20,ge=1,le=100)):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size  #changes here page-1
    total = db.employees.count_documents(q)  #changes here {} to q since we can check department wise
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code",1).skip(skip).limit(page_size))  #addeded sort
    # convert created_at to epoch miliseconds
    for item in items:
        if item.get("created_at") is not None:
            item["created_at"]=datetime_to_ms(item["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:                             #ADDED if new employee comes status notfound error 
        raise HTTPException(status_code=404,detail="Employee not found")

    # if body.status not in ["PRESENT","WFH","ON_DUTY"]:
        # raise HTTPException(status_code=422,detail="invalid status alowed vlaues are PRESENT`, `WFH` and `ON_DUTY")
    ts = (datetime.fromtimestamp(body.punched_at / 1000 ,tz=IST) 
          if body.punched_at is not None            #added time zone 
          else datetime.now(IST))
    ts=ts.replace(microsecond=0)
    h, m = map(int, emp["shift_start"].split(":"))
    shift_start_minutes = h * 60 + m

    end_h, end_m = map(int, emp["shift_end"].split(":"))
    shift_end_minutes = end_h * 60 + end_m

    attendance_date = ts.date()

    if shift_end_minutes <= shift_start_minutes:
        if ts.hour * 60 + ts.minute < shift_end_minutes:
            attendance_date -= timedelta(days=1)

    d = attendance_date.isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"],emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    # res = db.attendance_logs.insert_one(doc)
    # doc["id"] = str(res.inserted_id)
    # doc.pop("_id", None)
    # return doc
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409,detail="already punched in for this date")
    doc.pop("_id",None)
    reponse=doc.copy()
    reponse["punch_in"]=int(doc["punch_in"].timestamp()*1000)
    return reponse


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]] = None,
    page: int = Query(1,ge=1),
    page_size: int = Query(20,ge=1, le=100),
):
    if date_from and date_to and date_from>date_to:
        raise HTTPException(status_code=422,detail="date_from must not be after date_to")
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from.isoformat()
        if date_to:
            q["date"]["$lte"] = date_to.isoformat()
    if status:
        q["status"] = status
    total=db.attendance_logs.count_documents(q)
    items=list(db.attendance_logs.find(q,{"_id":0}).sort([("date",-1),("emp_code",1)])
               .skip((page-1)*page_size).limit(page_size))
    for item in items:
        item["punch_in"] = datetime_to_ms(item.get("punch_in"))
        item["punch_out"] = datetime_to_ms(item.get("punch_out"))

        for entry in item.get("history", []):
            entry["at"] = datetime_to_ms(entry.get("at"))

            for field, change in entry.get("changes", {}).items():
                if field in ("punch_in", "punch_out"):
                    change["from"] = datetime_to_ms(change.get("from"))
                    change["to"] = datetime_to_ms(change.get("to"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-out")
def punch_out(body:PunchOutIn):
    emp=db.employees.find_one({"emp_code":body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404,detail="Employee not found")
    ts=(datetime.fromtimestamp(body.punched_at/1000,tz=IST)
        if body.punched_at is not None
        else datetime.now(IST)).replace(microsecond=0)
    record=db.attendance_logs.find_one(
        {
            "emp_code":body.emp_code,
            "punch_in":{"$lte":ts}
        },sort=[("punch_in",-1),("_id",-1)])
    if record is None:
        raise HTTPException(status_code=404,detail="no punch is found")
    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409,detail="Alredy punche out")
    punch_in=record["punch_in"]
    if punch_in.tzinfo is None:
        punch_in=punch_in.replace(tzinfo=timezone.utc)
    punch_in=punch_in.astimezone(IST)
    elasped=(ts-punch_in).total_seconds()
    if elasped<=0 or elasped>24*60*60:
        raise HTTPException(status_code=422,detail="Invalid punch-out time")
    work_hours = compute_work_hours(punch_in, ts)

    overtime = compute_overtime(
        ts,emp["shift_start"],emp["shift_end"],record["date"])

    # R5: Compare the already-rounded work_hours.
    half_day = work_hours < 4.50
    # Only one concurrent request can update an open record.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day
            }
        }
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Already punched out"
        )

    # 9. Prepare the API response.
    record["punch_in"] = int(punch_in.timestamp() * 1000)
    record["punch_out"] = int(ts.timestamp() * 1000)
    record["work_hours"] = work_hours
    record["overtime_minutes"] = overtime
    record["half_day"] = half_day
    record.setdefault("late_minutes", 0)
    record.setdefault("history", [])

    # MongoDB's internal ID must not appear in the API response.
    record.pop("_id", None)
    return record


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    body: RegularizeRequest,
    attendance_date: date=Path(alias="date")
    
):
    # 1. Validate the employee and attendance record.
    emp = db.employees.find_one({"emp_code": emp_code})

    if emp is None:
        raise HTTPException(status_code=404,detail="Employee not found")

    record_date = attendance_date.isoformat()

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": record_date})

    if record is None:
        raise HTTPException(status_code=404,detail="Attendance record not found")

    # 2. Distinguish omitted fields from explicitly supplied nulls.
    supplied = body.model_fields_set

    if "status" in supplied and body.status is None:
        raise HTTPException(status_code=422,detail="status cannot be null")

    if "punch_in" in supplied and body.punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="punch_in cannot be null when supplied"
        )

    if "punch_out" in supplied and body.punch_out is None:
        raise HTTPException(
            status_code=422,
            detail="punch_out cannot be null when supplied"
        )

    old_status = record["status"]
    old_in_db = record.get("punch_in")
    old_out_db = record.get("punch_out")
    old_history = record.get("history", [])

    # MongoDB normally returns BSON datetimes as naive UTC.
    # Normalize instants to IST and whole seconds.
    def as_ist(value):
        if value is None:
            return None

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value.astimezone(IST).replace(microsecond=0)

    def to_ms(value):
        if value is None:
            return None

        return int(value.timestamp() * 1000)

    def normalize_history_value(value):
        if isinstance(value, datetime):
            return to_ms(value)
        return value

    old_in = as_ist(old_in_db)
    old_out = as_ist(old_out_db)

    # 3. Resolve the final status and punch times.
    new_status = (
        body.status if "status" in supplied else old_status
    )

    new_in = (
        datetime.fromtimestamp(body.punch_in / 1000, tz=IST)
        .replace(microsecond=0)
        if "punch_in" in supplied
        else old_in
    )

    new_out = (
        datetime.fromtimestamp(body.punch_out / 1000, tz=IST)
        .replace(microsecond=0)
        if "punch_out" in supplied
        else old_out
    )

    # 4. Validate status and punch-time combinations.
    if new_status in ("ABSENT", "LEAVE"):
        if new_in is not None and "punch_in" in supplied:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot have punch_in"
            )

        if new_out is not None and "punch_out" in supplied:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot have punch_out"
            )

        # A valid LEAVE or ABSENT record has no punch times.
        new_in = None
        new_out = None

    else:
        # PRESENT, WFH and ON_DUTY require a punch-in.
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="A presence status requires punch_in"
            )

        # 5. Verify that punch-in belongs to the attendance date.
        start_h, start_m = map(
            int, emp["shift_start"].split(":")
        )
        end_h, end_m = map(
            int, emp["shift_end"].split(":")
        )

        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        expected_date = new_in.date()

        # For an overnight shift, an early-morning punch-in
        # belongs to the previous attendance date.
        if end_minutes <= start_minutes:
            if new_in.hour * 60 + new_in.minute < end_minutes:
                expected_date -= timedelta(days=1)

        if expected_date.isoformat() != record_date:
            raise HTTPException(status_code=422,detail="punch_in must belong to the attendance date")

        # 6. Validate punch-out.
        if new_out is not None:
            elapsed_seconds = (new_out - new_in).total_seconds()

            if elapsed_seconds <= 0:
                raise HTTPException(status_code=422,detail="punch_out must be after punch_in")

            if elapsed_seconds > 24 * 60 * 60:
                raise HTTPException(status_code=422,detail="punch_out must be within 24 hours of punch_in")

    # 7. Recalculate derived fields from the final values.
    if new_in is None:
        late_minutes = 0
    else:
        late_minutes = compute_late_minutes(
            new_in,
            emp["shift_start"],
            emp["shift_end"]
        )

    if new_in is not None and new_out is not None:
        work_hours = compute_work_hours(new_in, new_out)

        overtime_minutes = compute_overtime(
            new_out,
            emp["shift_start"],
            emp["shift_end"],
            record_date
        )

        # Work hours are rounded before checking half-day.
        half_day = work_hours < 4.50
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    final_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day
    }

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False)
    }

    # 8. Build changes containing only genuinely changed fields.
    changes = {}

    for field, new_value in final_values.items():
        old_value = old_values[field]

        if field in ("punch_in", "punch_out"):
            old_comparison = to_ms(old_value)
            new_comparison = to_ms(new_value)
        else:
            old_comparison = old_value
            new_comparison = new_value

        if old_comparison != new_comparison:
            changes[field] = {
                "from": old_comparison,
                "to": new_comparison
            }

    # A no-op regularization is not allowed.
    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record"
        )

    # 9. Append exactly one audit-history entry.
    history_entry = {
        "at": datetime.now(IST).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    updated_history = old_history + [history_entry]

    # 10. Protect against concurrent edits.
    # Match the original values, including history, before updating.
    update_filter = {
        "_id": record["_id"],
        "status": old_status,
        "punch_in": old_in_db,
        "punch_out": old_out_db
    }

    if "history" in record:
        update_filter["history"] = old_history
    else:
        update_filter["history"] = {"$exists": False}

    update_values = dict(final_values)
    update_values["history"] = updated_history

    result = db.attendance_logs.update_one(
        update_filter,
        {"$set": update_values}
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Attendance record changed concurrently; "
                "please retry the request"
            )
        )

    # 11. Build the response without exposing MongoDB's _id.
    response = {
        "emp_code": emp_code,
        "date": record_date,
        "status": new_status,
        "punch_in": to_ms(new_in),
        "punch_out": to_ms(new_out),
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": []
    }

    # Serialize current and previous history timestamps.
    for entry in updated_history:
        serialized_entry = dict(entry)
        serialized_entry["at"] = normalize_history_value(
            entry.get("at")
        )

        serialized_changes = {}

        for field, change in entry.get("changes", {}).items():
            serialized_change = dict(change)

            if field in ("punch_in", "punch_out"):
                serialized_change["from"] = normalize_history_value(
                    serialized_change.get("from")
                )
                serialized_change["to"] = normalize_history_value(
                    serialized_change.get("to")
                )

            serialized_changes[field] = serialized_change

        serialized_entry["changes"] = serialized_changes
        response["history"].append(serialized_entry)

    return response

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    # Parse the requested month.
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    next_month = (
        month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)

    month_end = next_month - timedelta(days=1)

    start_str = month_start.isoformat()
    end_str = month_end.isoformat()

    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", start_str]},
                                    {"$lte": ["$date", end_str]}
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "date": 1,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                            "overtime_minutes": 1
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$set": {
                "joined_date": {
                    "$dateFromString": {"dateString": "$joined_on"}
                }
            }
        },
        {
            "$set": {
                "working_start": {
                    "$cond": [
                        {"$gt": ["$joined_date", datetime.combine(
                            month_start, datetime.min.time()
                        )]},
                        "$joined_date",
                        datetime.combine(month_start, datetime.min.time())
                    ]
                }
            }
        },
        {
            "$set": {
                "working_days": {
                    "$cond": [
                        {"$gt": ["$working_start", datetime.combine(
                            month_end, datetime.min.time()
                        )]},
                        0,
                        {
                            "$size": {
                                "$filter": {
                                    "input": {
                                        "$map": {
                                            "input": {
                                                "$range": [
                                                    0,
                                                    {
                                                        "$add": [
                                                            {
                                                                "$dateDiff": {
                                                                    "startDate": "$working_start",
                                                                    "endDate": datetime.combine(
                                                                        month_end,
                                                                        datetime.min.time()
                                                                    ),
                                                                    "unit": "day"
                                                                }
                                                            },
                                                            1
                                                        ]
                                                    }
                                                ]
                                            },
                                            "as": "day_offset",
                                            "in": {
                                                "$dateAdd": {
                                                    "startDate": "$working_start",
                                                    "unit": "day",
                                                    "amount": "$$day_offset"
                                                }
                                            }
                                        }
                                    },
                                    "as": "work_date",
                                    "cond": {
                                        "$not": [
                                            {
                                                "$in": [
                                                    {"$dayOfWeek": "$$work_date"},
                                                    [1, 7]
                                                ]
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        },
        {
            "$set": {
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$and": [
                                            {
                                                "$in": [
                                                    "$$log.status",
                                                    ["PRESENT", "WFH", "ON_DUTY"]
                                                ]
                                            },
                                            {
                                                "$not": [
                                                    {
                                                        "$in": [
                                                            {
                                                                "$dayOfWeek": {
                                                                    "$dateFromString": {
                                                                        "dateString": "$$log.date"
                                                                    }
                                                                }
                                                            },
                                                            [1, 7]
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1
                                ]
                            }
                        }
                    }
                },
                "leave_days": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "LEAVE"]}
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$gt": ["$$log.late_minutes", 0]}
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {"$ifNull": ["$$log.late_minutes", 0]}
                        }
                    }
                },
                "total_overtime_minutes": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {"$ifNull": ["$$log.overtime_minutes", 0]}
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "working_days": 1,
                "present_days": 1,
                "leave_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "total_overtime_minutes": 1,
                "attendance_pct": {
                    "$cond": [
                        {"$eq": ["$working_days", 0]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_days",
                                                            "$working_days"
                                                        ]
                                                    },
                                                    1000000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        }
                    ]
                }
            }
        }
    ]

    result = list(db.employees.aggregate(pipeline))

    if not result:
        raise HTTPException(status_code=404, detail="Employee not found")

    result[0]["month"] = month

    return result[0]

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    # Calculate the selected month's date range.
    month_start = datetime.strptime(month, "%Y-%m")
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")
    month_start_str = month_start.strftime("%Y-%m-%d")

    pipeline = [
        # Include employees who joined on or before month-end.
        {"$match": {"joined_on": {"$lte": month_end}}},

        # Fetch each employee's attendance records for the selected month.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", month_start_str]},
                                    {"$lte": ["$date", month_end]}
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "late_minutes": 1,
                            "overtime_minutes": 1,
                            "work_hours": 1
                        }
                    }
                ],
                "as": "attendance"
            }
        },

        # Filter attendance records by their required categories.
        {
            "$set": {
                "present_records": {
                    "$filter": {
                        "input": "$attendance",
                        "as": "record",
                        "cond": {
                            "$in": [
                                "$$record.status",
                                ["PRESENT", "WFH", "ON_DUTY"]
                            ]
                        }
                    }
                },
                "leave_records": {
                    "$filter": {
                        "input": "$attendance",
                        "as": "record",
                        "cond": {"$eq": ["$$record.status", "LEAVE"]}
                    }
                },
                "on_duty_records": {
                    "$filter": {
                        "input": "$attendance",
                        "as": "record",
                        "cond": {"$eq": ["$$record.status", "ON_DUTY"]}
                    }
                },
                "late_records": {
                    "$filter": {
                        "input": "$attendance",
                        "as": "record",
                        "cond": {"$gt": ["$$record.late_minutes", 0]}
                    }
                },
                "work_hour_records": {
                    "$filter": {
                        "input": "$attendance",
                        "as": "record",
                        "cond": {
                            "$and": [
                                {
                                    "$in": [
                                        "$$record.status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]
                                },
                                {"$ne": ["$$record.work_hours", None]}
                            ]
                        }
                    }
                }
            }
        },

        # Calculate employee-level totals before grouping by department.
        {
            "$set": {
                "present_days_employee": {"$size": "$present_records"},
                "leave_count_employee": {"$size": "$leave_records"},
                "on_duty_count_employee": {"$size": "$on_duty_records"},
                "late_count_employee": {"$size": "$late_records"},
                "late_minutes_employee": {
                    "$sum": {
                        "$map": {
                            "input": "$attendance",
                            "as": "record",
                            "in": {"$ifNull": ["$$record.late_minutes", 0]}
                        }
                    }
                },
                "work_hours_values": {
                    "$map": {
                        "input": "$work_hour_records",
                        "as": "record",
                        "in": "$$record.work_hours"
                    }
                }
            }
        },
        {
            "$set": {
                "work_hours_sum_employee": {"$sum": "$work_hours_values"},
                "work_hours_count_employee": {"$size": "$work_hours_values"}
            }
        },

        # Aggregate employee and attendance totals for each department.
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days_employee"},
                "leave_count": {"$sum": "$leave_count_employee"},
                "on_duty_count": {"$sum": "$on_duty_count_employee"},
                "late_count": {"$sum": "$late_count_employee"},
                "total_late_minutes": {"$sum": "$late_minutes_employee"},
                "work_hours_sum": {"$sum": "$work_hours_sum_employee"},
                "work_hours_count": {"$sum": "$work_hours_count_employee"}
            }
        },

        # Round the average to two decimals using half-up rounding.
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "month": {"$literal": month},
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$work_hours_sum",
                                                            "$work_hours_count"
                                                        ]
                                                    },
                                                    100
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                100
                            ]
                        },
                        None
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    return list(db.employees.aggregate(pipeline))

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = Query(None)
):
    month_start = datetime.strptime(month, "%Y-%m")
    next_month = (
        month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)

    start_date = month_start.strftime("%Y-%m-%d")
    end_date = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")

    pipeline = [
        # Select attendance records for the month that were late.
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date},
                "late_minutes": {"$gt": 0}
            }
        },

        # Aggregate total late minutes and number of late records per employee.
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },

        # Get employee details; ignore logs without a matching employee.
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},

        # Apply department filtering BEFORE ranking.
        *(
            [{"$match": {"employee.department": department}}]
            if department is not None
            else []
        ),

        # Competition ranking: ties share a rank and the next rank is skipped.
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}}
            }
        },

        # Apply limit to ranks, not the number of rows.
        {"$match": {"rank": {"$lte": limit}}},

        # Sort tied employees by emp_code.
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },

        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        }
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    # Validate date range: maximum 92 calendar days, inclusive.
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must not be before 'from'",
        )

    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # 404 if the department does not exist.
    if not db.employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    start = datetime.combine(
        from_date, datetime.min.time(), tzinfo=timezone.utc
    )
    end_exclusive = datetime.combine(
        to_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )

    pipeline = [
        # Generate the calendar inside MongoDB.
        {"$documents": [{"date": start}]},
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start, end_exclusive],
                },
            }
        },
        {
            "$set": {
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                        "timezone": "UTC",
                    }
                },
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$date"},
                                [1, 7],
                            ]
                        }
                    ]
                },
            }
        },

        # Count employees who have joined by each date.
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": ["$joined_on", "$$day"]
                            },
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {
                            "$arrayElemAt": [
                                "$headcount_result.count", 0
                            ]
                        },
                        0,
                    ]
                }
            }
        },

        # Fetch attendance records for this department and day.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "date": {
                                "$gte": from_date.isoformat(),
                                "$lte": to_date.isoformat(),
                            },
                            "$expr": {"$eq": ["$date", "$$day"]},
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {"$match": {"employee.department": department}},
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "daily_logs",
            }
        },

        # Calculate daily presence and late counts.
        {
            "$set": {
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$daily_logs",
                                    "as": "log",
                                    "cond": {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$daily_logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0,
                                ]
                            },
                        }
                    }
                },
            }
        },

        # Rate is null on weekends or when headcount is zero.
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount",
                                                        ]
                                                    },
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },

        # Average non-null rates over this row and six prior rows.
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_average_raw": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_average_raw", None]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_average_raw",
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    try:
        items = list(db.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(
            status_code=500,
            detail="Failed to calculate department trend",
        )

    return {
        "department": department,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "items": items,
    }

def get_execution_explain(collection_name: str, pipeline: list):
    return db.command(
        "explain",
        {
            "aggregate": collection_name,
            "pipeline": pipeline,
            "cursor": {}
        },
        verbosity="executionStats"
    )

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(
        None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    ),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[
        Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
    ] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must not be after date_to",
            )

        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from.isoformat()

            if date_to:
                query["date"]["$lte"] = date_to.isoformat()

        if status:
            query["status"] = status

        explain_result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result,
        }
    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required for employee_monthly",
            )

        try:
            month_start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        next_month = (
            month_start.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

        month_end = next_month - timedelta(days=1)

        start_str = month_start.isoformat()
        end_str = month_end.isoformat()

        # Build the same aggregation pipeline as employee_monthly.
        pipeline = [
            {"$match": {"emp_code": emp_code}},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$employee_code"]},
                                        {"$gte": ["$date", start_str]},
                                        {"$lte": ["$date", end_str]},
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "date": 1,
                                "status": 1,
                                "half_day": 1,
                                "late_minutes": 1,
                                "overtime_minutes": 1,
                            }
                        },
                    ],
                    "as": "logs",
                }
            },
            {
                "$set": {
                    "joined_date": {
                        "$dateFromString": {"dateString": "$joined_on"}
                    }
                }
            },
            {
                "$set": {
                    "working_start": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$joined_date",
                                    datetime.combine(
                                        month_start, datetime.min.time()
                                    ),
                                ]
                            },
                            "$joined_date",
                            datetime.combine(
                                month_start, datetime.min.time()
                            ),
                        ]
                    }
                }
            },
            {
                "$set": {
                    "working_days": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$working_start",
                                    datetime.combine(
                                        month_end, datetime.min.time()
                                    ),
                                ]
                            },
                            0,
                            {
                                "$size": {
                                    "$filter": {
                                        "input": {
                                            "$map": {
                                                "input": {
                                                    "$range": [
                                                        0,
                                                        {
                                                            "$add": [
                                                                {
                                                                    "$dateDiff": {
                                                                        "startDate": "$working_start",
                                                                        "endDate": datetime.combine(
                                                                            month_end,
                                                                            datetime.min.time(),
                                                                        ),
                                                                        "unit": "day",
                                                                    }
                                                                },
                                                                1,
                                                            ]
                                                        },
                                                    ]
                                                },
                                                "as": "day_offset",
                                                "in": {
                                                    "$dateAdd": {
                                                        "startDate": "$working_start",
                                                        "unit": "day",
                                                        "amount": "$$day_offset",
                                                    }
                                                },
                                            }
                                        },
                                        "as": "work_date",
                                        "cond": {
                                            "$not": [
                                                {
                                                    "$in": [
                                                        {"$dayOfWeek": "$$work_date"},
                                                        [1, 7],
                                                    ]
                                                }
                                            ]
                                        },
                                    }
                                }
                            },
                        ]
                    }
                }
            },
            {
                "$set": {
                    "present_days": {
                        "$sum": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$logs",
                                        "as": "log",
                                        "cond": {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$$log.status",
                                                        ["PRESENT", "WFH", "ON_DUTY"],
                                                    ]
                                                },
                                                {
                                                    "$not": [
                                                        {
                                                            "$in": [
                                                                {
                                                                    "$dayOfWeek": {
                                                                        "$dateFromString": {
                                                                            "dateString": "$$log.date"
                                                                        }
                                                                    }
                                                                },
                                                                [1, 7],
                                                            ]
                                                        }
                                                    ]
                                                },
                                            ]
                                        },
                                    }
                                },
                                "as": "log",
                                "in": {
                                    "$cond": [
                                        {"$eq": ["$$log.half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                            }
                        }
                    },
                    "leave_days": {
                        "$size": {
                            "$filter": {
                                "input": "$logs",
                                "as": "log",
                                "cond": {"$eq": ["$$log.status", "LEAVE"]},
                            }
                        }
                    },
                    "late_count": {
                        "$size": {
                            "$filter": {
                                "input": "$logs",
                                "as": "log",
                                "cond": {"$gt": ["$$log.late_minutes", 0]},
                            }
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {
                            "$map": {
                                "input": "$logs",
                                "as": "log",
                                "in": {"$ifNull": ["$$log.late_minutes", 0]},
                            }
                        }
                    },
                    "total_overtime_minutes": {
                        "$sum": {
                            "$map": {
                                "input": "$logs",
                                "as": "log",
                                "in": {"$ifNull": ["$$log.overtime_minutes", 0]},
                            }
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "emp_code": 1,
                    "working_days": 1,
                    "present_days": 1,
                    "leave_days": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "total_overtime_minutes": 1,
                    "attendance_pct": {
                        "$cond": [
                            {"$eq": ["$working_days", 0]},
                            None,
                            {
                                "$divide": [
                                    {
                                        "$floor": {
                                            "$add": [
                                                {
                                                    "$multiply": [
                                                        {
                                                            "$divide": [
                                                                "$present_days",
                                                                "$working_days",
                                                            ]
                                                        },
                                                        1000000,
                                                    ]
                                                },
                                                0.5,
                                            ]
                                        }
                                    },
                                    10000,
                                ]
                            },
                        ]
                    },
                }
            },
        ]

        explain_result = get_execution_explain("employees", pipeline)

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result,
        }
    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for department_summary",
            )

        try:
            month_start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        next_month = (
            month_start.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

        month_end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")
        month_start_str = month_start.strftime("%Y-%m-%d")

        pipeline = [
            {"$match": {"joined_on": {"$lte": month_end}}},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$employee_code"]},
                                        {"$gte": ["$date", month_start_str]},
                                        {"$lte": ["$date", month_end]},
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "status": 1,
                                "late_minutes": 1,
                                "overtime_minutes": 1,
                                "work_hours": 1,
                            }
                        },
                    ],
                    "as": "attendance",
                }
            },
            {
                "$set": {
                    "present_records": {
                        "$filter": {
                            "input": "$attendance",
                            "as": "record",
                            "cond": {
                                "$in": [
                                    "$$record.status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                        }
                    },
                    "leave_records": {
                        "$filter": {
                            "input": "$attendance",
                            "as": "record",
                            "cond": {"$eq": ["$$record.status", "LEAVE"]},
                        }
                    },
                    "on_duty_records": {
                        "$filter": {
                            "input": "$attendance",
                            "as": "record",
                            "cond": {"$eq": ["$$record.status", "ON_DUTY"]},
                        }
                    },
                    "late_records": {
                        "$filter": {
                            "input": "$attendance",
                            "as": "record",
                            "cond": {"$gt": ["$$record.late_minutes", 0]},
                        }
                    },
                    "work_hour_records": {
                        "$filter": {
                            "input": "$attendance",
                            "as": "record",
                            "cond": {
                                "$and": [
                                    {
                                        "$in": [
                                            "$$record.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": ["$$record.work_hours", None]},
                                ]
                            },
                        }
                    },
                }
            },
            {
                "$set": {
                    "present_days_employee": {"$size": "$present_records"},
                    "leave_count_employee": {"$size": "$leave_records"},
                    "on_duty_count_employee": {"$size": "$on_duty_records"},
                    "late_count_employee": {"$size": "$late_records"},
                    "late_minutes_employee": {
                        "$sum": {
                            "$map": {
                                "input": "$attendance",
                                "as": "record",
                                "in": {"$ifNull": ["$$record.late_minutes", 0]},
                            }
                        }
                    },
                    "work_hours_values": {
                        "$map": {
                            "input": "$work_hour_records",
                            "as": "record",
                            "in": "$$record.work_hours",
                        }
                    },
                }
            },
            {
                "$set": {
                    "work_hours_sum_employee": {"$sum": "$work_hours_values"},
                    "work_hours_count_employee": {"$size": "$work_hours_values"},
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "headcount": {"$sum": 1},
                    "present_days": {"$sum": "$present_days_employee"},
                    "leave_count": {"$sum": "$leave_count_employee"},
                    "on_duty_count": {"$sum": "$on_duty_count_employee"},
                    "late_count": {"$sum": "$late_count_employee"},
                    "total_late_minutes": {"$sum": "$late_minutes_employee"},
                    "work_hours_sum": {"$sum": "$work_hours_sum_employee"},
                    "work_hours_count": {"$sum": "$work_hours_count_employee"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "month": {"$literal": month},
                    "headcount": 1,
                    "present_days": 1,
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$work_hours_count", 0]},
                            {
                                "$divide": [
                                    {
                                        "$floor": {
                                            "$add": [
                                                {
                                                    "$multiply": [
                                                        {
                                                            "$divide": [
                                                                "$work_hours_sum",
                                                                "$work_hours_count",
                                                            ]
                                                        },
                                                        100,
                                                    ]
                                                },
                                                0.5,
                                            ]
                                        }
                                    },
                                    100,
                                ]
                            },
                            None,
                        ]
                    },
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]

        explain_result = get_execution_explain("employees", pipeline)

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result,
        }
    if endpoint == "late_leaderboard":
        if month is None:
            raise HTTPException(
                status_code=422,
                detail="month is required for late_leaderboard"
            )

        month_start = datetime.strptime(month, "%Y-%m")
        next_month = (
            month_start.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

        start_date = month_start.strftime("%Y-%m-%d")
        end_date = (
            next_month - timedelta(days=1)
        ).strftime("%Y-%m-%d")

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": start_date,
                        "$lte": end_date
                    },
                    "late_minutes": {"$gt": 0}
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {
                        "$sum": "$late_minutes"
                    },
                    "late_count": {"$sum": 1}
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"},
        ]

        # Filter department before calculating ranks.
        if department is not None:
            pipeline.append({
                "$match": {
                    "employee.department": department
                }
            })

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}}
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "_id": 1
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1
                }
            }
        ])

        explain_result = get_execution_explain(
            "attendance_logs",
            pipeline
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result
        }
    if endpoint == "department_trend":
        if department is None or from_date is None or to_date is None:
            raise HTTPException(
                status_code=422,
                detail="department, from and to are required"
            )

        if to_date < from_date:
            raise HTTPException(
                status_code=422,
                detail="'to' must not be before 'from'"
            )

        if (to_date - from_date).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days"
            )

        if not db.employees.find_one({"department": department}):
            raise HTTPException(
                status_code=404,
                detail="Department not found"
            )

        start = datetime.combine(
            from_date, datetime.min.time(), tzinfo=timezone.utc
        )
        end_exclusive = datetime.combine(
            to_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )

        pipeline = [
            {"$documents": [{"date": start}]},
            {
                "$densify": {
                    "field": "date",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start, end_exclusive],
                    },
                }
            },
            {
                "$set": {
                    "date_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date",
                            "timezone": "UTC",
                        }
                    },
                    "is_working_day": {
                        "$not": [
                            {
                                "$in": [
                                    {"$dayOfWeek": "$date"},
                                    [1, 7],
                                ]
                            }
                        ]
                    },
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$date_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "department": department,
                                "$expr": {
                                    "$lte": ["$joined_on", "$$day"]
                                },
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "headcount_result",
                }
            },
            {
                "$set": {
                    "headcount": {
                        "$ifNull": [
                            {
                                "$arrayElemAt": [
                                    "$headcount_result.count", 0
                                ]
                            },
                            0,
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"day": "$date_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "date": {
                                    "$gte": from_date.isoformat(),
                                    "$lte": to_date.isoformat(),
                                },
                                "$expr": {
                                    "$eq": ["$date", "$$day"]
                                },
                            }
                        },
                        {
                            "$lookup": {
                                "from": "employees",
                                "localField": "emp_code",
                                "foreignField": "emp_code",
                                "as": "employee",
                            }
                        },
                        {"$unwind": "$employee"},
                        {
                            "$match": {
                                "employee.department": department
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "status": 1,
                                "half_day": 1,
                                "late_minutes": 1,
                            }
                        },
                    ],
                    "as": "daily_logs",
                }
            },
            {
                "$set": {
                    "present_count": {
                        "$sum": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$daily_logs",
                                        "as": "log",
                                        "cond": {
                                            "$in": [
                                                "$$log.status",
                                                [
                                                    "PRESENT",
                                                    "WFH",
                                                    "ON_DUTY",
                                                ],
                                            ]
                                        },
                                    }
                                },
                                "as": "log",
                                "in": {
                                    "$cond": [
                                        {
                                            "$eq": [
                                                "$$log.half_day", True
                                            ]
                                        },
                                        0.5,
                                        1,
                                    ]
                                },
                            }
                        }
                    },
                    "late_count": {
                        "$size": {
                            "$filter": {
                                "input": "$daily_logs",
                                "as": "log",
                                "cond": {
                                    "$gt": [
                                        {
                                            "$ifNull": [
                                                "$$log.late_minutes", 0
                                            ]
                                        },
                                        0,
                                    ]
                                },
                            }
                        }
                    },
                }
            },
            {
                "$set": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]},
                                ]
                            },
                            {
                                "$divide": [
                                    {
                                        "$floor": {
                                            "$add": [
                                                {
                                                    "$multiply": [
                                                        {
                                                            "$divide": [
                                                                "$present_count",
                                                                "$headcount",
                                                            ]
                                                        },
                                                        10000,
                                                    ]
                                                },
                                                0.5,
                                            ]
                                        }
                                    },
                                    10000,
                                ]
                            },
                            None,
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "moving_average_raw": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
            {
                "$set": {
                    "moving_avg_7d": {
                        "$cond": [
                            {"$eq": ["$moving_average_raw", None]},
                            None,
                            {
                                "$divide": [
                                    {
                                        "$floor": {
                                            "$add": [
                                                {
                                                    "$multiply": [
                                                        "$moving_average_raw",
                                                        10000,
                                                    ]
                                                },
                                                0.5,
                                            ]
                                        }
                                    },
                                    10000,
                                ]
                            },
                        ]
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$date_string",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

        explain_result = db.command(
            "explain",
            {
                "aggregate": 1,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "database",
            "explain": explain_result,
        }
    raise HTTPException(
        status_code=501,
        detail="Explain plan for this endpoint has not been added yet",
    )
