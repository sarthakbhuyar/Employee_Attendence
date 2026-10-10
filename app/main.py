"""Employee Attendance & Analytics API.

All datetimes in MongoDB are UTC BSON dates; the HTTP API uses epoch milliseconds.
"""
import calendar
import os
import re
from datetime import date as Date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
PRESENT = {"PRESENT", "WFH", "ON_DUTY"}
EPOCH = Annotated[StrictInt, Field(ge=100000000000, le=4102444800000)]

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=False)
db = client[os.getenv("MONGO_DB", "attendance_db")]


def create_indexes() -> None:
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="employee_code_unique")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="attendance_day_unique")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_listing")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("late_minutes", DESCENDING)], name="employee_month_metrics")


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def startup() -> None:
    create_indexes()


def bad(detail: str) -> None:
    raise HTTPException(422, detail)


def parse_day(value: str) -> Date:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return Date.fromisoformat(value)
    except ValueError:
        bad("invalid date")


def parse_month(value: str) -> tuple[Date, Date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        bad("invalid month")
    year, month = map(int, value.split("-"))
    return Date(year, month, 1), Date(year, month, calendar.monthrange(year, month)[1])


def to_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def epoch_ms(value: Optional[datetime]) -> Optional[int]:
    return None if value is None else int(to_utc(value).timestamp() * 1000)


def instant(value: Optional[int]) -> datetime:
    now = datetime.now(UTC) if value is None else datetime.fromtimestamp(value / 1000, UTC)
    return now.replace(microsecond=0, tzinfo=None)  # PyMongo's default BSON representation


def clock(value: str) -> tuple[int, int]:
    h, m = map(int, value.split(":"))
    return h, m


def is_overnight(employee: dict) -> bool:
    return clock(employee["shift_end"]) <= clock(employee["shift_start"])


def attendance_day(punch_in: datetime, employee: dict) -> str:
    local = to_utc(punch_in).astimezone(IST)
    d = local.date()
    if is_overnight(employee) and (local.hour, local.minute) < clock(employee["shift_end"]):
        d -= timedelta(days=1)
    return d.isoformat()


def shift_instant(day: str, time_value: str, next_day: bool = False) -> datetime:
    d = parse_day(day) + timedelta(days=1 if next_day else 0)
    h, m = clock(time_value)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=IST).astimezone(UTC).replace(tzinfo=None)


def half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def late_minutes(punch_in: datetime, employee: dict, day: str) -> int:
    elapsed = (punch_in - shift_instant(day, employee["shift_start"])).total_seconds()
    return int(elapsed // 60) if elapsed > 600 else 0


def derived(punch_in: Optional[datetime], punch_out: Optional[datetime], employee: dict, day: str) -> dict:
    if not punch_in:
        return {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    result = {"work_hours": None, "late_minutes": late_minutes(punch_in, employee, day), "overtime_minutes": 0, "half_day": False}
    if punch_out:
        hours = half_up((punch_out - punch_in).total_seconds() / 3600)
        end = shift_instant(day, employee["shift_end"], is_overnight(employee))
        minutes = int((punch_out - end).total_seconds() // 60)
        result.update(work_hours=hours, overtime_minutes=minutes if minutes >= 30 else 0, half_day=hours < 4.5)
    return result


def serialise(value):
    if isinstance(value, datetime):
        return epoch_ms(value)
    if isinstance(value, list):
        return [serialise(v) for v in value]
    if isinstance(value, dict):
        return {k: serialise(v) for k, v in value.items() if k != "_id"}
    return value


def attendance_response(doc: dict) -> dict:
    doc = dict(doc)
    doc.setdefault("history", [])
    doc.setdefault("half_day", False)
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    return serialise(doc)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EmployeeIn(APIModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined(cls, v): parse_day(v); return v

    @model_validator(mode="after")
    def distinct_shift(self):
        if self.shift_start == self.shift_end: raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(APIModel):
    emp_code: str
    punched_at: Optional[EPOCH] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(APIModel):
    emp_code: str
    punched_at: Optional[EPOCH] = None


class RegularizeIn(APIModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[EPOCH] = None
    punch_out: Optional[EPOCH] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


@app.get("/health")
def health():
    try:
        db.command("ping")
    except Exception:
        raise HTTPException(503, "MongoDB unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = instant(None)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialise(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department else {}
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size))
    return {"items": [serialise(x) for x in items], "total": db.employees.count_documents(q), "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee: raise HTTPException(404, "employee not found")
    when = instant(body.punched_at)
    day = attendance_day(when, employee)
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": when, "punch_out": None,
           **derived(when, None, employee, day), "history": []}
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return attendance_response(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    if not db.employees.find_one({"emp_code": body.emp_code}): raise HTTPException(404, "employee not found")
    when = instant(body.punched_at)
    record = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": when}}, sort=[("punch_in", DESCENDING)])
    if not record: raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None: raise HTTPException(409, "record already punched out")
    if when <= record["punch_in"] or when - record["punch_in"] > timedelta(hours=24): bad("punch_out must be after punch_in and within 24 hours")
    employee = db.employees.find_one({"emp_code": body.emp_code})
    values = {"punch_out": when, **derived(record["punch_in"], when, employee, record["date"])}
    updated = db.attendance_logs.find_one_and_update({"_id": record["_id"], "punch_out": None}, {"$set": values}, return_document=True)
    if not updated: raise HTTPException(409, "record already punched out")
    return attendance_response(updated)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if date_from: parse_day(date_from)
    if date_to: parse_day(date_to)
    if date_from and date_to and date_from > date_to: bad("date_from must not exceed date_to")
    q = {}
    if emp_code: q["emp_code"] = emp_code
    if status: q["status"] = status
    if date_from or date_to: q["date"] = {**({"$gte": date_from} if date_from else {}), **({"$lte": date_to} if date_to else {})}
    items = list(db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page-1)*page_size).limit(page_size))
    return {"items": [attendance_response(x) for x in items], "total": db.attendance_logs.count_documents(q), "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{day}")
def regularize(emp_code: str, day: str, body: RegularizeIn):
    parse_day(day)
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee: raise HTTPException(404, "employee not found")
    old = db.attendance_logs.find_one({"emp_code": emp_code, "date": day})
    if not old: raise HTTPException(404, "attendance record not found")
    supplied = body.model_fields_set
    new_status = body.status if "status" in supplied else old["status"]
    new_in = instant(body.punch_in) if "punch_in" in supplied else old.get("punch_in")
    new_out = instant(body.punch_out) if "punch_out" in supplied else old.get("punch_out")
    if new_status not in PRESENT:
        if "punch_in" in supplied or "punch_out" in supplied: bad("absent or leave cannot include punch times")
        new_in = new_out = None
    elif not new_in:
        bad("a presence status requires punch_in")
    if new_in and attendance_day(new_in, employee) != day: bad("punch_in does not belong to this attendance date")
    if new_out and (new_out <= new_in or new_out - new_in > timedelta(hours=24)): bad("punch_out must be after punch_in and within 24 hours")
    new_derived = derived(new_in, new_out, employee, day)
    fields = {"status": new_status, "punch_in": new_in, "punch_out": new_out, **new_derived}
    changes = {}
    for key, value in fields.items():
        before = old.get(key, False if key == "half_day" else 0 if key in {"late_minutes", "overtime_minutes"} else None)
        if before != value: changes[key] = {"from": before, "to": value}
    if not changes: bad("request changes nothing")
    history = {"at": instant(None), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    guard = {"_id": old["_id"], "status": old["status"], "punch_in": old.get("punch_in"), "punch_out": old.get("punch_out")}
    updated = db.attendance_logs.find_one_and_update(guard, {"$set": fields, "$push": {"history": history}}, return_document=True)
    if not updated: raise HTTPException(409, "record changed concurrently")
    return attendance_response(updated)


def weekday_expression(field="$date"):
    return {"$and": [{"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": field}}}, 2]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": field}}}, 6]}]}


def metric_group():
    present = {"$in": ["$status", list(PRESENT)]}
    working = weekday_expression()
    return {"$group": {"_id": None,
        "present_days": {"$sum": {"$cond": [{"$and": [present, working]}, {"$cond": [{"$ifNull": ["$half_day", False]}, .5, 1]}, 0]}},
        "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}}}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    first, last = parse_month(month)
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee: raise HTTPException(404, "employee not found")
    result = list(db.attendance_logs.aggregate([{"$match": {"emp_code": emp_code, "date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}}, metric_group()]))
    metrics = result[0] if result else {}
    begin = max(first, parse_day(employee["joined_on"]))
    working = 0
    if begin <= last:
        day_count = (last - begin).days + 1
        day_numbers = {"$range": [0, day_count]}
        weekdays = {"$map": {
            "input": day_numbers,
            "as": "offset",
            "in": {"$dayOfWeek": {"$dateAdd": {
                "startDate": datetime.combine(begin, datetime.min.time()),
                "unit": "day",
                "amount": "$$offset",
            }}},
        }}
        count_result = list(db.attendance_logs.aggregate([
            {"$documents": [{"_id": None}]},
            {"$project": {"working_days": {"$size": {"$filter": {
                "input": weekdays,
                "as": "weekday",
                "cond": {"$and": [{"$gte": ["$$weekday", 2]}, {"$lte": ["$$weekday", 6]}]},
            }}}}},
        ]))
        working = count_result[0]["working_days"] if count_result else 0
    present_days = metrics.get("present_days", 0)
    return {"emp_code": emp_code, "month": month, "working_days": working, "present_days": present_days,
            "leave_days": metrics.get("leave_days", 0), "late_count": metrics.get("late_count", 0),
            "total_late_minutes": metrics.get("total_late_minutes", 0), "total_overtime_minutes": metrics.get("total_overtime_minutes", 0),
            "attendance_pct": half_up(present_days / working * 100, 4) if working else None}


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    _, last = parse_month(month)
    match = {"joined_on": {"$lte": last.isoformat()}}
    if department: match["department"] = department
    pipeline = [{"$match": match}, {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
        {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", month + "-01"]}, {"$lte": ["$date", month + "-31"]}]}}}, {"$project": {"status": 1, "date": 1, "work_hours": 1, "half_day": 1, "late_minutes": 1}}], "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$department", "headcount_set": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT)]}, weekday_expression("$logs.date")]}, {"$cond": [{"$ifNull": ["$logs.half_day", False]}, .5, 1]}, 0]}},
            "work_sum": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT)]}, {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}]}, "$logs.work_hours", 0]}},
            "work_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT)]}, {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}}, "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}}, "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$headcount_set"}, "present_days": 1, "avg_work_hours": {"$cond": [{"$gt": ["$work_count", 0]}, {"$divide": ["$work_sum", "$work_count"]}, None]}, "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}}, {"$sort": {"department": 1}}]
    items = list(db.employees.aggregate(pipeline))
    for item in items:
        if item["avg_work_hours"] is not None: item["avg_work_hours"] = half_up(item["avg_work_hours"])
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    parse_month(month)
    pipeline = [{"$match": {"date": {"$gte": month + "-01", "$lte": month + "-31"}}}, {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}}, {"$match": {"total_late_minutes": {"$gt": 0}}}, {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}}, {"$unwind": "$employee"}]
    if department: pipeline.append({"$match": {"employee.department": department}})
    pipeline += [{"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}}, {"$match": {"rank": {"$lte": limit}}}, {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}}, {"$sort": {"total_late_minutes": -1, "emp_code": 1}}]
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(alias="from"), to: str = Query(...)):
    start, end = parse_day(from_), parse_day(to)
    if end < start: bad("to must not be before from")
    if (end - start).days > 91: bad("range may not exceed 92 days")
    if not db.employees.find_one({"department": department}): raise HTTPException(404, "department not found")
    # Generate the complete range inside MongoDB, then join both collections for each day.
    rounded = lambda field: {"$cond": [
        {"$ne": [field, None]},
        {"$divide": [{"$floor": {"$add": [{"$multiply": [field, 10000]}, 0.5]}}, 10000]},
        None,
    ]}
    people_lookup = {"$lookup": {"from": "employees", "let": {"day": "$date"}, "pipeline": [
        {"$match": {"$expr": {"$and": [{"$eq": ["$department", department]}, {"$lte": ["$joined_on", "$$day"]}]}}},
        {"$project": {"emp_code": 1}},
    ], "as": "people"}}
    logs_lookup = {"$lookup": {"from": "attendance_logs", "let": {"day": "$date", "codes": "$people.emp_code"}, "pipeline": [
        {"$match": {"$expr": {"$and": [{"$eq": ["$date", "$$day"]}, {"$in": ["$emp_code", "$$codes"]}]}}},
    ], "as": "logs"}}
    date_series = {"$map": {
        "input": {"$range": [0, (end - start).days + 1]},
        "as": "n",
        "in": {"$dateToString": {"format": "%Y-%m-%d", "date": {"$dateAdd": {
            "startDate": "$start", "unit": "day", "amount": "$$n"
        }}}},
    }}
    pipeline = [
        {"$documents": [{"start": datetime.combine(start, datetime.min.time())}]},
        {"$project": {"dates": date_series}},
        {"$unwind": "$dates"}, {"$project": {"_id": 0, "date": "$dates"}}, people_lookup, logs_lookup,
        {"$project": {"date": 1, "headcount": {"$size": "$people"}, "logs": 1}},
        {"$project": {"date": 1, "headcount": 1,
            "present_count": {"$sum": {"$map": {"input": "$logs", "as": "log", "in": {"$cond": [{"$in": ["$$log.status", list(PRESENT)]}, {"$cond": [{"$ifNull": ["$$log.half_day", False]}, .5, 1]}, 0]}}}},
            "late_count": {"$size": {"$filter": {"input": "$logs", "as": "log", "cond": {"$gt": [{"$ifNull": ["$$log.late_minutes", 0]}, 0]}}}}}},
        {"$set": {"is_working_day": weekday_expression()}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, {"$divide": ["$present_count", "$headcount"]}, None]}}},
        {"$set": {"attendance_rate": rounded("$attendance_rate")}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$set": {"moving_avg_7d": rounded("$moving_avg_7d")}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
        {"$sort": {"date": 1}},
    ]
    return {"department": department, "items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/admin/explain/{endpoint}")
def explain(endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"], emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), limit: int = Query(10, ge=1, le=50), from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    # The explain uses the same indexed leading query as its corresponding endpoint.
    if endpoint == "attendance_list":
        q = {}; 
        if emp_code: q["emp_code"] = emp_code
        if status: q["status"] = status
        if date_from or date_to: q["date"] = {**({"$gte": date_from} if date_from else {}), **({"$lte": date_to} if date_to else {})}
        command = {"find": "attendance_logs", "filter": q, "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}; collection = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month: bad("emp_code and month are required")
        command = {"aggregate": "attendance_logs", "pipeline": [{"$match": {"emp_code": emp_code, "date": {"$gte": month+"-01", "$lte": month+"-31"}}}, metric_group()], "cursor": {}}; collection = "attendance_logs"
    elif endpoint == "late_leaderboard":
        if not month: bad("month is required")
        command = {"aggregate": "attendance_logs", "pipeline": [{"$match": {"date": {"$gte": month+"-01", "$lte": month+"-31"}}}], "cursor": {}}; collection = "attendance_logs"
    elif endpoint == "department_summary":
        if not month: bad("month is required")
        _, last = parse_month(month); q = {"joined_on": {"$lte": last.isoformat()}}; q.update({"department": department} if department else {})
        command = {"aggregate": "employees", "pipeline": [{"$match": q}], "cursor": {}}; collection = "employees"
    else:
        if not department or not from_ or not to: bad("department, from and to are required")
        command = {"find": "employees", "filter": {"department": department, "joined_on": {"$lte": to}}}; collection = "employees"
    result = db.command({"explain": command, "verbosity": "executionStats"})
    return {"endpoint": endpoint, "collection": collection, "explain": serialise(result)}


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="frontend")
