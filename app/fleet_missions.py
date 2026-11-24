"""Work orders for non-delivery journeys; the delivery ledger is read-only here."""

from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, service_hours
from . import mission_policy as policy

router = APIRouter(prefix="/api/fleet-missions", tags=["fleet missions"])


def deny(message="لا تملك صلاحية هذا الإجراء."):
    raise db.error(403, "MISSION_FORBIDDEN", message)


def invalid(message):
    raise db.error(422, "MISSION_INPUT", message)


def conflict(message):
    raise db.error(409, "MISSION_STATE", message)


def actor(c, u, lead=False):
    role = policy.profile(c, u)
    if not role or lead and role not in policy.LEADS:
        deny()
    return role


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Mission(Form):
    vehicle_id: str = Field(max_length=150)
    employee_id: str = Field(max_length=150)
    kind: str = Field(max_length=40)
    destination: str = Field(default="", max_length=350)
    planned_minutes: int = Field(ge=1, le=2880)
    scheduled_at: datetime
    stops: list[str] = Field(default_factory=list, max_length=100)


class Action(Form):
    action: Literal[
        "approve",
        "issue",
        "start",
        "finish",
        "request_return",
        "return",
        "cancel",
        "arrive",
        "miss",
    ]
    km: int | None = Field(default=None, ge=0, le=10000000)
    broken: bool = False
    fuel: int | None = Field(default=None, ge=0, le=100)
    note: str = Field(default="", max_length=400)
    inspection: str = Field(default="", max_length=200)
    stop_id: str = Field(default="", max_length=150)


class Route(Form):
    stops: list[str] = Field(min_length=1, max_length=100)


def roster(c, brand):
    return c.execute(
        "SELECT s.profile,u.id,u.display_name FROM fleet_mission_staff s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND u.active AND u.role<>'customer' ORDER BY u.display_name",
        (brand,),
    ).fetchall()


def vehicle(c, brand, vid):
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('physical-vehicle:'||v.plate_key,0)) FROM fleet_vehicles v WHERE v.brand_id=%s AND v.id=%s",
        (brand, vid),
    )
    r = c.execute(
        "SELECT m.*,v.details,v.plate,v.plate_key FROM fleet_mission_cars m JOIN fleet_vehicles v ON v.id=m.vehicle_id WHERE m.brand_id=%s AND m.vehicle_id=%s FOR UPDATE OF m,v",
        (brand, vid),
    ).fetchone()
    if not r:
        invalid("اختر سيارة مسجلة ومتاحة.")
    return r


def stop_list(c, brand, ids):
    if not ids or len(ids) != len(set(ids)):
        invalid("اختر فروعًا مختلفة بالترتيب.")
    data = c.execute(
        "SELECT id,name,branch_no FROM branches WHERE brand_id=%s AND id=ANY(%s)",
        (brand, ids),
    ).fetchall()
    indexed = {r["id"]: r for r in data}
    if len(indexed) != len(ids):
        invalid("أحد الفروع غير متاح.")
    return [dict(indexed[i], state="pending") for i in ids]


@router.put("/routes/{kind}")
def save_route(kind: str, body: Route, user=Depends(auth.current_user)):
    with db.tx() as c:
        if actor(c, user) not in ("owner", "director"):
            deny()
        if kind not in policy.SUPPLY:
            invalid("اختر خط توريد.")
        stops = stop_list(c, user["brand_id"], body.stops)
        c.execute(
            "INSERT INTO fleet_supply_routes VALUES(%s,%s,%s,%s) ON CONFLICT(brand_id,kind) DO UPDATE SET stops=EXCLUDED.stops,updated_by=EXCLUDED.updated_by",
            (user["brand_id"], kind, db.json(stops), user["id"]),
        )
        db.audit(
            c, user["id"], "fleet.route.save", "fleet", kind, {"stops": body.stops}
        )
        return {"saved": True}


@router.get("/workspace")
def workspace(user=Depends(auth.current_user)):
    with db.tx() as c:
        role = actor(c, user)
        brand = user["brand_id"]
        lead = role in policy.LEADS
        staff = roster(c, brand)
        if not lead:
            staff = [r for r in staff if r["id"] == user["id"]]
        cars = c.execute(
            "SELECT m.*,v.plate,v.details->>'type' model,u.display_name holder FROM fleet_mission_cars m JOIN fleet_vehicles v ON v.id=m.vehicle_id LEFT JOIN users u ON u.id=m.key_holder WHERE m.brand_id=%s ORDER BY v.plate",
            (brand,),
        ).fetchall()
        for car in cars:
            car["options"] = {
                s["id"]: [
                    k
                    for k in policy.KINDS
                    if policy.allowed(
                        s["profile"], car["category"], car["home_profile"], k
                    )
                ]
                for s in staff
            }
        visible = "m.brand_id=%s AND (m.status NOT IN ('cancelled','completed') OR (m.status='completed' AND m.returned_at IS NULL AND v.key_holder=m.employee_id AND v.category<>'permanent') OR m.operating_day=%s)"
        params = [brand, service_hours.delivery_operational_date()]
        if not lead:
            visible += " AND m.employee_id=%s"
            params.append(user["id"])
        missions = c.execute(
            "SELECT m.*,u.display_name,v.key_holder,v.category,car.plate FROM fleet_missions m JOIN users u ON u.id=m.employee_id JOIN fleet_mission_cars v ON v.vehicle_id=m.vehicle_id JOIN fleet_vehicles car ON car.id=v.vehicle_id WHERE "
            + visible
            + " ORDER BY m.scheduled_at DESC,m.id DESC",
            params,
        ).fetchall()
        return {
            "role": role,
            "self": user["id"],
            "staff": staff,
            "kinds": policy.KINDS,
            "missions": missions,
            "cars": [v for v in cars if lead or any(v["options"].values())],
            "branches": c.execute(
                "SELECT id,name,branch_no FROM branches WHERE brand_id=%s ORDER BY branch_no",
                (brand,),
            ).fetchall(),
            "routes": c.execute(
                "SELECT kind,stops FROM fleet_supply_routes WHERE brand_id=%s", (brand,)
            ).fetchall(),
        }


@router.post("/orders")
def create(body: Mission, request: Request, user=Depends(auth.current_user)):
    with db.tx() as c:
        role = actor(c, user)
        brand = user["brand_id"]
        lead = role in policy.LEADS
        if not lead and body.employee_id != user["id"]:
            deny()

        def perform():
            car = vehicle(c, brand, body.vehicle_id)
            person = next(
                (s for s in roster(c, brand) if s["id"] == body.employee_id), None
            )
            if not person or not policy.allowed(
                person["profile"], car["category"], car["home_profile"], body.kind
            ):
                deny("السيارة غير مخصصة لهذا الموظف أو لهذه المهمة.")
            if car["details"].get("operating_status") == "paused":
                conflict("السيارة موقوفة.")
            if body.scheduled_at.tzinfo is None:
                invalid("حدد وقت الانطلاق بتوقيت السعودية.")
            kind = body.kind
            destination = body.destination
            stops = []
            parent = None
            if kind in policy.SUPPLY:
                if kind == "selected" and body.stops and role in ("director", "owner"):
                    stops = stop_list(c, brand, body.stops)
                else:
                    route = c.execute(
                        "SELECT stops FROM fleet_supply_routes WHERE brand_id=%s AND kind=%s",
                        (brand, kind),
                    ).fetchone()
                    if not route:
                        invalid("يلزم حفظ فروع خط التوريد أولًا.")
                    stops = route["stops"]
                destination = " • ".join(s["name"] for s in stops)
            if kind == "assignment":
                if role not in ("owner", "director"):
                    deny()
                parent = c.execute(
                    "SELECT id FROM fleet_missions WHERE employee_id=%s AND vehicle_id=%s AND kind='supervision' AND status='active'",
                    (body.employee_id, car["vehicle_id"]),
                ).fetchone()
                if not parent:
                    invalid("يفتح المشرف يومه أولًا.")
            if kind == "supervision" and not destination:
                destination = "جولات الإشراف اليومية"
            if not destination:
                invalid(
                    "اكتب السبب عند اختيار أخرى."
                    if kind == "other"
                    else "حدد الوجهة أو الغرض."
                )
            approved = (
                lead
                or kind == "supervision"
                and car["home_profile"] == person["profile"]
            )
            stamp = service_hours.now_riyadh()
            order = c.execute(
                """INSERT INTO fleet_missions(brand_id,vehicle_id,employee_id,kind,destination,stops,planned_minutes,scheduled_at,requested_by,approved_by,approved_at,operating_day,status,parent_id)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    brand,
                    car["vehicle_id"],
                    body.employee_id,
                    kind,
                    destination,
                    db.json(stops),
                    body.planned_minutes,
                    body.scheduled_at,
                    user["id"],
                    user["id"] if approved else None,
                    stamp if approved else None,
                    service_hours.delivery_operational_date(body.scheduled_at),
                    "approved" if approved else "requested",
                    parent["id"] if parent else None,
                ),
            ).fetchone()
            return {"id": order["id"]}

        return db.idempotent(
            c,
            user["id"],
            request.headers.get("Idempotency-Key"),
            "missions.create:" + brand,
            body.model_dump(mode="json"),
            perform,
        )


@router.post("/orders/{oid}")
def transition(
    oid: int, body: Action, request: Request, user=Depends(auth.current_user)
):
    with db.tx() as c:
        role = actor(c, user)
        brand = user["brand_id"]
        lead = role in policy.LEADS
        m = c.execute(
            "SELECT * FROM fleet_missions WHERE brand_id=%s AND id=%s FOR UPDATE",
            (brand, oid),
        ).fetchone()
        if not m or not lead and m["employee_id"] != user["id"]:
            deny()

        def perform():
            car = vehicle(c, brand, m["vehicle_id"])
            a = body.action
            state = m["status"]
            uid = user["id"]
            emp = m["employee_id"]
            if a in ("approve", "issue", "return") and not lead:
                deny()
            if (
                a in ("start", "finish", "arrive", "miss", "request_return")
                and emp != uid
            ):
                deny()
            if a == "cancel" and (
                state in ("requested", "approved") or state == "issued" and lead
            ):
                c.execute(
                    "UPDATE fleet_missions SET status='cancelled',outcome=%s WHERE id=%s",
                    (body.note, oid),
                )
            elif a == "approve" and state == "requested":
                c.execute(
                    "UPDATE fleet_missions SET status='approved',approved_by=%s,approved_at=now() WHERE id=%s",
                    (uid, oid),
                )
            elif a == "issue" and state == "approved" and not m["parent_id"]:
                if car["key_holder"] is not None:
                    conflict("يجب تسليم المفتاح للإدارة قبل صرفه من جديد.")
                check_available(c, m, car)
                c.execute(
                    "UPDATE fleet_missions SET status='issued',issued_by=%s,issued_at=now() WHERE id=%s",
                    (uid, oid),
                )
            elif a == "start" and (
                state == "issued"
                or state == "approved"
                and car["key_holder"] == emp
                and (car["category"] == "permanent" or m["parent_id"])
            ):
                check_available(c, m, car)
                floor = (
                    car["last_km"]
                    if car["last_km"] is not None
                    else car["details"].get("odometer_km")
                )
                if not body.broken and (
                    body.km is None or floor is not None and body.km < int(floor)
                ):
                    invalid("أدخل قراءة عداد صحيحة.")
                if body.fuel is None or not body.inspection:
                    invalid("أكمل فحص السيارة ومستوى البنزين.")
                c.execute(
                    "UPDATE fleet_missions SET status='active',started_at=now(),operating_day=%s,start_km=%s,start_fuel=%s,inspection=%s WHERE id=%s",
                    (
                        service_hours.delivery_operational_date(),
                        None if body.broken else body.km,
                        body.fuel,
                        body.inspection,
                        oid,
                    ),
                )
                c.execute(
                    "UPDATE fleet_mission_cars SET key_holder=%s,broken_meter=%s,last_km=coalesce(%s,last_km) WHERE vehicle_id=%s",
                    (
                        emp,
                        body.broken,
                        None if body.broken else body.km,
                        car["vehicle_id"],
                    ),
                )
            elif a in ("arrive", "miss") and state == "active":
                if a == "miss" and not body.note:
                    invalid("اكتب سبب تعذر التوريد.")
                stops = m["stops"]
                stop = next((s for s in stops if s["state"] == "pending"), None)
                if not stop or stop["id"] != body.stop_id:
                    conflict("اختر المحطة التالية حسب ترتيب الرحلة.")
                stop.update(
                    state="done" if a == "arrive" else "missed",
                    note=body.note,
                    time=service_hours.now_riyadh().isoformat(),
                )
                c.execute(
                    "UPDATE fleet_missions SET stops=%s WHERE id=%s",
                    (db.json(stops), oid),
                )
            elif a == "finish" and state == "active":
                if any(s["state"] == "pending" for s in m["stops"]):
                    invalid("سجّل نتيجة المحطات المتبقية.")
                if c.execute(
                    "SELECT 1 FROM fleet_missions WHERE parent_id=%s AND status NOT IN ('completed','cancelled')",
                    (oid,),
                ).fetchone():
                    conflict("أكمل التكليف المرتبط باليوم أولًا.")
                if body.fuel is None:
                    invalid("سجّل بنزين نهاية المهمة.")
                if not body.broken and (
                    body.km is None
                    or body.km < max(car["last_km"] or 0, m["start_km"] or 0)
                ):
                    invalid("تحقق من قراءة نهاية المهمة.")
                if m["kind"] == "supervision" and not body.note:
                    invalid("اكتب الفروع التي زرتها اليوم.")
                km = None if body.broken else body.km
                c.execute(
                    "UPDATE fleet_missions SET status='completed',ended_at=now(),end_km=%s,end_fuel=%s,outcome=%s WHERE id=%s",
                    (km, body.fuel, body.note, oid),
                )
                c.execute(
                    "UPDATE fleet_mission_cars SET broken_meter=%s,last_km=coalesce(%s,last_km) WHERE vehicle_id=%s",
                    (body.broken, km, car["vehicle_id"]),
                )
            elif (
                a in ("request_return", "return")
                and state == "completed"
                and car["key_holder"] == emp
                and not m["parent_id"]
                and not m["returned_at"]
            ):
                if car["category"] == "permanent":
                    conflict("مفتاح المشرف بعهدته الدائمة.")
                if c.execute(
                    "SELECT 1 FROM fleet_missions WHERE vehicle_id=%s AND (status IN ('issued','active') OR (id<>%s AND started_at>%s))",
                    (car["vehicle_id"], oid, m["started_at"]),
                ).fetchone():
                    conflict("استخدم أحدث أمر للسيارة بعد انتهاء المهمة.")
                if a == "request_return":
                    c.execute(
                        "UPDATE fleet_missions SET return_requested_at=now() WHERE id=%s",
                        (oid,),
                    )
                elif m["return_requested_at"]:
                    c.execute(
                        "UPDATE fleet_missions SET returned_at=now(),returned_by=%s WHERE id=%s",
                        (uid, oid),
                    )
                    c.execute(
                        "UPDATE fleet_mission_cars SET key_holder=NULL WHERE vehicle_id=%s",
                        (car["vehicle_id"],),
                    )
                else:
                    conflict("يلزم طلب تسليم المفتاح من الموظف.")
            else:
                conflict("تغيّرت حالة الأمر. حدّث الشاشة.")
            db.audit(
                c,
                uid,
                "fleet.mission." + a,
                "fleet_mission",
                str(oid),
                {"brand_id": brand},
            )
            return {"id": oid, "action": a}

        return db.idempotent(
            c,
            user["id"],
            request.headers.get("Idempotency-Key"),
            "missions." + str(oid),
            body.model_dump(),
            perform,
        )


def check_available(c, m, car):
    if car["details"].get("operating_status") == "paused":
        conflict("السيارة موقوفة.")
    if c.execute(
        "SELECT 1 FROM fleet_operating_segments l JOIN fleet_vehicles v ON v.id=l.vehicle_id WHERE v.plate_key=%s AND l.returned_at IS NULL",
        (car["plate_key"],),
    ).fetchone():
        conflict("السيارة في شفت توصيل.")
    if c.execute(
        """SELECT 1 FROM fleet_missions WHERE (vehicle_id=%s OR employee_id=%s) AND id<>%s
                    AND status IN ('issued','active') AND (%s::bigint IS NULL OR id<>%s)""",
        (car["vehicle_id"], m["employee_id"], m["id"], m["parent_id"], m["parent_id"]),
    ).fetchone():
        conflict("السيارة أو الموظف في مهمة أخرى.")
    if (
        m["parent_id"]
        and not c.execute(
            "SELECT 1 FROM fleet_missions WHERE id=%s AND status='active' FOR UPDATE",
            (m["parent_id"],),
        ).fetchone()
    ):
        conflict("يوم المشرف مقفل.")
