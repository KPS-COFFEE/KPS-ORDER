import io
import uuid
from datetime import timedelta
from zipfile import ZipFile

from app import (
    auth,
    db,
    fleet_mission_reports as reports,
    mission_policy as policy,
    service_hours,
)
from app.main import app


def test_vehicle_policy():
    for kind in policy.SUPPLY:
        assert all(
            policy.allowed(p, "cold_van", None, kind) for p in policy.COLD_DRIVERS
        )
        assert [
            p
            for p in (
                "supplier_a",
                "supplier_b",
                "supplier_bus",
                "director",
                "purchaser",
            )
            if policy.allowed(p, "cold_truck", None, kind)
        ] == ["supplier_a"]
        assert not policy.allowed("supplier_bus", "staff_bus", "supplier_bus", kind)
    assert policy.allowed("supplier_bus", "staff_bus", "supplier_bus", "bus")
    assert not policy.allowed("supplier_b", "staff_bus", "supplier_bus", "bus")
    assert all(
        policy.allowed(p, "shared_camry", None, "visit")
        for p in ("fleet_manager", "director")
    )
    assert not policy.allowed("purchaser", "shared_camry", None, "visit")
    assert policy.allowed("purchaser", "assigned", "purchaser", "purchase")
    assert not policy.allowed("internal", "assigned", "purchaser", "visit")


def test_mission_lifecycle_and_reports(client, monkeypatch):
    people = [
        ("manager", "fleet_manager"),
        ("buyer", "purchaser"),
        ("gm", "director"),
        ("stranger", None),
        ("external", "supervisor_a"),
    ]
    cfg = {
        "kps": {
            "people": [{"profile": p, "id": u} for u, p in people if p],
            "cars": [
                dict(
                    plate="1234 ABD",
                    model="Van",
                    category="assigned",
                    home="purchaser",
                    art=11,
                ),
                dict(
                    plate="د د ر 1235",
                    model="Car",
                    category="permanent",
                    home="supervisor_a",
                    art=1,
                ),
            ],
        }
    }
    monkeypatch.setattr(policy, "config", lambda: cfg)
    with db.tx() as c:
        for uid, _ in people:
            c.execute(
                "INSERT INTO users(id,role,brand_id,display_name) VALUES(%s,'delivery_staff','kps',%s)",
                (uid, uid),
            )
        policy.sync(c)
        policy.sync(c)

    def login(uid):
        app.dependency_overrides[auth.current_user] = lambda: {
            "id": uid,
            "brand_id": "kps",
            "role": "delivery_staff",
        }

    def post(path, body):
        return client.post(
            "/api/fleet-missions/" + path,
            json=body,
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )

    login("stranger")
    assert client.get("/api/fleet-missions/workspace").status_code == 403
    login("buyer")
    w = client.get("/api/fleet-missions/workspace").json()
    assert len(w["cars"]) == 1
    day = service_hours.delivery_operational_date()
    body = dict(
        employee_id="buyer",
        vehicle_id=w["cars"][0]["vehicle_id"],
        kind="other",
        scheduled_at=service_hours.now_riyadh().isoformat(),
        planned_minutes=60,
    )
    assert post("orders", body).status_code == 422
    order = post("orders", dict(body, destination="مهمة تجريبية"))
    assert order.status_code == 200, order.text
    path = "orders/" + str(order.json()["id"])
    assert post(path, {"action": "approve"}).status_code == 403
    login("manager")
    for act in ["approve", "issue"]:
        assert post(path, {"action": act}).status_code == 200
    login("buyer")
    assert (
        post(
            path, dict(action="start", inspection="سليمة", fuel=75, km=122)
        ).status_code
        == 200
    )
    assert post(path, dict(action="finish", fuel=50, km=110)).status_code == 422
    assert post(path, dict(action="finish", fuel=50, broken=True)).status_code == 200
    assert post(path, dict(action="request_return")).status_code == 200
    assert (
        client.get(
            "/api/fleet-missions/reports/missions", params={"day": day}
        ).status_code
        == 403
    )
    login("manager")
    assert post(path, dict(action="return")).status_code == 200

    def fetch(group, fmt):
        return client.get(
            "/api/fleet-missions/reports/" + group, params={"day": day, "fmt": fmt}
        )

    assert fetch("missions", "json").json()["rows"][0]["distance"] == "عداد معطل"
    assert fetch("delivery", "json").json()["rows"] == []
    assert fetch("missions", "pdf").content.startswith(b"%PDF")
    assert (
        "xl/workbook.xml"
        in ZipFile(io.BytesIO(fetch("missions", "xlsx").content)).namelist()
    )
    t = service_hours.now_riyadh().replace(hour=7, minute=50) + timedelta(days=1)
    monkeypatch.setattr(service_hours, "now_riyadh", lambda: t)
    reports.tick()
    reports.tick()
    with db.tx() as c:
        assert (
            c.execute("SELECT count(*) n FROM fleet_morning_reports").fetchone()["n"]
            == 1
        )
    login("external")
    w = client.get("/api/fleet-missions/workspace").json()
    car = w["cars"][0]
    assert car["key_holder"] == "external"
    body.update(
        kind="supervision", employee_id="external", vehicle_id=car["vehicle_id"]
    )
    oid = post("orders", body).json()["id"]
    assert (
        post(
            "orders/" + str(oid),
            dict(action="start", inspection="سليمة", fuel=50, broken=True),
        ).status_code
        == 200
    )


def test_scheduled_director_mail(database, monkeypatch):
    import base64
    from app import email_delivery
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    email = "fleet@example.test"
    monkeypatch.setattr(
        policy, "config", lambda: {"kps": dict(report_email=email, people=[], cars=[])}
    )
    monkeypatch.setenv(
        "MAIL_OUTBOX_KEY",
        base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode(),
    )
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "test-only")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.test")
    with db.tx() as c:
        c.execute(
            "INSERT INTO users(id,role,brand_id,display_name) VALUES('gm','delivery_manager','kps','Director')"
        )
        c.execute("INSERT INTO fleet_mission_staff VALUES('kps','gm','director')")
        c.execute(
            "INSERT INTO manager_notification_subscriptions(id,user_id,brand_id,recipient_email) VALUES('gms','gm','kps',%s)",
            (email,),
        )
    t = service_hours.now_riyadh().replace(hour=7, minute=49)
    monkeypatch.setattr(service_hours, "now_riyadh", lambda: t)
    reports.tick()
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM fleet_morning_reports").fetchone()
    t = t.replace(minute=50)
    reports.tick()
    reports.tick()
    with db.tx() as c:
        rows = c.execute(
            "SELECT * FROM email_outbox WHERE kind='fleet_daily'"
        ).fetchall()
        assert len(rows) == 1 and rows[0]["recipient"] == email
        payload = email_delivery._payload(rows[0])
        assert len(payload["attachments"]) == 2
        assert all(
            base64.b64decode(i["content"]).startswith(b"%PDF")
            for i in payload["attachments"]
        )
        c.execute(
            "UPDATE manager_notification_subscriptions SET enabled=false WHERE id='gms'"
        )
    monkeypatch.setattr(
        email_delivery,
        "_send",
        lambda row: (_ for _ in ()).throw(AssertionError("recipient revoked")),
    )
    email_delivery.dispatch_once()
    with db.tx() as c:
        assert (
            c.execute(
                "SELECT status FROM email_outbox WHERE kind='fleet_daily'"
            ).fetchone()["status"]
            == "cancelled"
        )
