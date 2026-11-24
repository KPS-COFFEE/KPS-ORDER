"""Daily operational snapshots and exports for the two fleet registers."""

import asyncio
import base64
import io
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, Response
from fastapi.encoders import jsonable_encoder
from . import (
    accounting_export,
    auth,
    db,
    email_delivery,
    fleet,
    mission_policy,
    service_hours,
)
from .fleet_missions import actor, router

STATUS = dict(
    requested="بانتظار الموافقة",
    approved="معتمد",
    issued="جاهز للاستلام",
    active="على الطريق",
    completed="مكتمل",
    cancelled="ملغى",
    idle="بلا أمر",
)
TITLES = {"missions": "أوامر حركة سيارات المؤسسة", "delivery": "شفتات توصيل الطلبات"}
DAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
LABELS = dict(
    id="الأمر / الشفت",
    model="السيارة",
    plate="اللوحة",
    employee="الموظف",
    task="المهمة والوجهة",
    status="الحالة",
    start="الخروج",
    end="العودة",
    plan="المخطط بالدقائق",
    minutes="المدة بالدقائق",
    distance="المسافة كم",
    custody="عهدة المفتاح",
    details="تفاصيل المهمة",
)
ASSETS = Path(__file__).resolve().parents[1] / "public/assets"


def duration(start, end):
    return max(0, int((end - start).total_seconds() // 60)) if start and end else None


def clock(t):
    return service_hours.local_time(t).strftime("%H:%M") if t else "—"


def snapshot(c, brand, day, now):
    missions = c.execute(
        """SELECT m.*, v.plate, concat_ws(' · ',v.details->>'type',nullif(v.details->>'color','')) model, mc.art,
                  u.display_name employee, issuer.display_name issuer
        FROM fleet_missions m JOIN fleet_mission_cars mc ON mc.vehicle_id=m.vehicle_id
        JOIN fleet_vehicles v ON v.id=m.vehicle_id JOIN users u ON u.id=m.employee_id
        LEFT JOIN users issuer ON issuer.id=m.issued_by
        WHERE m.brand_id=%s AND m.operating_day=%s ORDER BY m.scheduled_at,m.id""",
        (brand, day),
    ).fetchall()
    result = []
    for m in missions:
        km = (
            m["end_km"] - m["start_km"]
            if m["start_km"] is not None and m["end_km"] is not None
            else (
                "عداد معطل"
                if m["started_at"]
                and (m["start_km"] is None or m["ended_at"] and m["end_km"] is None)
                else "—"
            )
        )
        custody = (
            "أعيد للإدارة"
            if m["returned_at"]
            else (
                "طُلب إرجاعه"
                if m["return_requested_at"]
                else (
                    "بعهدة الموظف"
                    if m["started_at"]
                    else "جاهز للاستلام" if m["issued_at"] else "—"
                )
            )
        )
        report_detail = " • ".join(
            filter(
                None,
                [
                    m["inspection"],
                    m["outcome"],
                    *(
                        s["name"]
                        + " ("
                        + {"pending": "مجدول", "done": "تم", "missed": "تعذر"}[
                            s["state"]
                        ]
                        + ") "
                        + s.get("note", "")
                        for s in m["stops"]
                    ),
                    *(
                        "بنزين " + str(v) + "٪"
                        for v in (m["start_fuel"], m["end_fuel"])
                        if v is not None
                    ),
                ],
            )
        )
        segments = m.get("vehicle_changes") or []
        segment_notes = []
        for leg in segments:
            measured = leg["end"] - leg["start"] if leg["end"] is not None and leg["start"] is not None else None
            segment_notes.append(f"{leg['plate']} · {measured if measured is not None else 'غير مسجل'} كم")
        if segments:
            segment_notes.append(f"{m['plate']} · {km} كم")
            if isinstance(km,(int,float)) and all(s["end"] is not None and s["start"] is not None for s in segments):
                km += sum(s["end"]-s["start"] for s in segments)
            else:
                km = "غير مكتمل"
        result.append(
            dict(
                id=str(m["id"]),
                model=m["model"] or "مركبة",
                art=m["art"],
                plate=" / ".join([s["plate"] for s in segments]+[m["plate"]]),
                employee=m["employee"],
                employee_id=m["employee_id"],
                vehicle_ids=[s["vehicle_id"] for s in segments]+[m["vehicle_id"]],
                kind=m["kind"],
                code=m["status"],
                status=STATUS[m["status"]],
                task=mission_policy.KINDS[m["kind"]] + " · " + m["destination"] + (" · "+" / ".join(segment_notes) if segments else "") + (" · "+m["outcome"] if m["outcome"] else ""),
                start=clock(m["started_at"]),
                end=clock(m["ended_at"]),
                plan=m["planned_minutes"],
                minutes=duration(m["started_at"], m["ended_at"] or now),
                distance=km,
                custody=custody,
                details=report_detail,
                parent=m["parent_id"],
            )
        )
    shifts = c.execute(
        """SELECT s.id,s.user_id,u.display_name employee,s.opened_at,coalesce(s.submitted_at,s.closed_at) ended,
        l.vehicle_id,v.plate,concat_ws(' · ',v.details->>'type',nullif(v.details->>'color','')) model,mc.art, """
        + fleet.METRICS
        + """ metrics
        FROM shifts s JOIN users u ON u.id=s.user_id
        LEFT JOIN fleet_operating_segments l ON l.shift_id=s.id LEFT JOIN fleet_vehicles v ON v.id=l.vehicle_id
        LEFT JOIN fleet_mission_cars mc ON mc.vehicle_id=v.id
        LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=s.id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) correction ON true
        LEFT JOIN LATERAL(SELECT expected_km FROM fleet_route_readings WHERE shift_id=s.id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) route_reading ON true
        WHERE s.brand_id=%s AND s.kind='driver' AND s.reporting_operational_date=%s
        ORDER BY s.opened_at,s.id,l.segment_no""",
        (brand, day),
    ).fetchall()
    grouped = {}
    for s in shifts:
        r = grouped.setdefault(
            s["id"],
            dict(
                id=s["id"],
                employee=s["employee"],
                employee_id=s["user_id"],
                model="",
                plate="",
                art=s["art"],
                vehicle_ids=[],
                kind="delivery",
                code="completed" if s["ended"] else "active",
                status="مكتمل" if s["ended"] else "مفتوح",
                minutes=duration(s["opened_at"], s["ended"] or now),
                distance=0,
            ),
        )
        if s["vehicle_id"] and s["vehicle_id"] not in r["vehicle_ids"]:
            r["vehicle_ids"].append(s["vehicle_id"])
            r["plate"] = " / ".join(filter(None, [r["plate"], s["plate"]]))
            r["model"] = " / ".join(filter(None, [r["model"], s["model"] or "مركبة"]))
        measure = s["metrics"] or {}
        km = (
            measure.get("actual_distance")
            if measure.get("distance_eligible", True)
            else None
        )
        r["distance"] = (
            r["distance"] + float(km)
            if isinstance(r["distance"], (int, float)) and km is not None
            else "غير مكتمل"
        )
    used = {v for row in [*result, *grouped.values()] for v in row["vehicle_ids"]}
    inventory = c.execute(
        """SELECT m.vehicle_id,m.art,v.plate,concat_ws(' · ',v.details->>'type',nullif(v.details->>'color','')) model
        FROM fleet_mission_cars m JOIN fleet_vehicles v ON v.id=m.vehicle_id
        WHERE m.brand_id=%s AND m.category<>'relief' ORDER BY v.plate""",
        (brand,),
    ).fetchall()
    for car in inventory:
        if car["vehicle_id"] not in used:
            result.append(
                dict(
                    id="—",
                    employee="—",
                    employee_id="",
                    kind="",
                    code="idle",
                    status=STATUS["idle"],
                    vehicle_ids=[car["vehicle_id"]],
                    model=car["model"] or "مركبة",
                    plate=car["plate"],
                    art=car["art"],
                    task="لا يوجد أمر مسجل",
                    start="—",
                    end="—",
                    plan=None,
                    minutes=None,
                    distance="—",
                    custody="—",
                    details="",
                )
            )
    return {
        "missions": result,
        "delivery": list(grouped.values()),
        "as_of": now.isoformat(),
    }


def columns(group, extra=False):
    if group == "delivery":
        return ["employee", "model", "plate", "minutes", "distance"]
    keys = [
        "id",
        "model",
        "plate",
        "employee",
        "task",
        "start",
        "end",
        "plan",
        "minutes",
        "distance",
        "status",
    ]
    return keys + ["custody", "details"] if extra else keys


def title(group, day):
    return f"{TITLES[group]} - {DAYS[day.weekday()]} {day.isoformat()}"


def make_pdf(rows, group, day):
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A3
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    from textwrap import wrap

    font = "FleetKufi"
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(font, str(ASSETS / "fonts/NotoKufiArabic-Regular.ttf"))
        )
    out = io.BytesIO()
    size = landscape(A3)
    w, h = size
    pdf = canvas.Canvas(out, pagesize=size)
    pdf.setTitle(title(group, day))

    def visual(s):
        return get_display(arabic_reshaper.reshape(str(s)))

    def write(s, x, y, fs=11, ink="#193E42"):
        pdf.setFillColor(colors.HexColor(ink))
        pdf.setFont(font, fs)
        pdf.drawRightString(x, y, visual(s))

    if group == "missions":
        from reportlab.platypus import Table, TableStyle, Image

        def cell(value, width, size=13):
            value = "—" if value is None or value == "" else str(value)
            # Wrap logical Arabic words before shaping each printed line.
            lines = []
            for paragraph in value.split("\n"):
                words = paragraph.split()
                line = ""
                for word in words:
                    candidate = (line + " " + word).strip()
                    if line and pdfmetrics.stringWidth(visual(candidate), font, size) > width - 14:
                        lines.append(visual(line))
                        line = ""
                    line = (line + " " + word).strip()
                lines.append(visual(line))
            return "\n".join(lines)

        by_car = {}
        for row in rows:
            key = tuple(row.get("vehicle_ids") or [row.get("plate")])
            by_car.setdefault(key, []).append(row)
        widths = [94, 88, 73, 78, 76, 76, 272, 185, w - 50 - 942]
        heads = ["الحالة", "المسافة كم", "المدة د", "المخطط د", "العودة", "الخروج",
                 "الأمر والموظف والمهمة", "السيارة واللوحة", ""]
        table_rows = [[visual(label) for label in heads]]
        styles = [
            ("FONT", (0, 0), (-1, -1), font, 14, 21),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#193e42")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#193e42")),
            ("FONT", (1, 1), (5, -1), font, 17, 23),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]
        for i, car_rows in enumerate(by_car.values()):
            first = len(table_rows)
            for row in car_rows:
                fields = [row.get(k) for k in ("status","distance","minutes","plan","end","start")]
                fields.append(f"{row['id']} · {row['employee']}\n{row['task']}")
                plate = str(row.get("plate") or "—")
                parts = re.fullmatch(r"\s*(\d{1,4})\s*([ابحدرسصطعقكلمنهوى](?:\s*[ابحدرسصطعقكلمنهوى]){2})\s*", plate)
                if parts:
                    digits, letters = parts.groups()
                    mapping = dict(zip("ابحدرسصطعقكلمنهوى", "ABJDRSXTEGKLZNHUV"))
                    latin = " ".join(mapping[ch] for ch in reversed(letters) if ch.strip())
                    plate = plate + "\n" + digits + "   " + latin
                fields.append(f"{row['model']}\n{plate}")
                cells = [cell(v, widths[j], 17 if 1 <= j <= 5 else 14) for j, v in enumerate(fields)]
                table_rows.append(cells + [""])
            last = len(table_rows) - 1
            for col in (7, 8):
                if first != last:
                    styles.append(("SPAN", (col, first), (col, last)))
            if i % 2 == 0:
                styles.append(("BACKGROUND", (0, first), (-1, last), colors.HexColor("#f1f6f4")))
            styles.append(("LINEBELOW", (0, last), (-1, last), .5, colors.HexColor("#cbdad5")))
            art = ASSETS / "fleet-models" / f"{car_rows[0].get('art')}.jpg"
            if art.is_file():
                table_rows[first][-1] = Image(str(art), width=95, height=40, kind="proportional")
        table = Table(table_rows, colWidths=widths, style=TableStyle(styles))
        _, height = table.wrap(w - 50, h)
        h = max(h, height + 220)
        pdf.setPageSize((w, h))
        write(title(group, day), w - 26, h - 44, 24)
        finished = [r for r in rows if r.get("code") == "completed" and not r.get("parent")]
        minutes = sum(r.get("minutes") or 0 for r in finished)
        km = sum(r["distance"] for r in finished if isinstance(r.get("distance"), (int, float)))
        cards = [("السيارات التي خرجت", sum(any(r.get("start") not in (None,"","—") for r in group_rows) for group_rows in by_car.values())),
                 ("أوامر الحركة", sum(r.get("code") != "idle" for r in rows)),
                 ("مدة المهام المكتملة", f"{minutes // 60} س {minutes % 60} د"),
                 ("المسافة المسجلة", f"{km:g} كم")]
        for i, (label, value) in enumerate(cards):
            right = w - 25 - i * (w - 50) / 4
            left = right - (w - 50) / 4 + 7
            pdf.setFillColor(colors.HexColor("#edf5f1"))
            pdf.roundRect(left, h - 143, right-left, 75, 8, stroke=0, fill=1)
            write(label, right - 12, h - 94, 14)
            write(str(value), right - 12, h - 129, 21)
        table.drawOn(pdf, 25, h - 158 - height)
        write("الصور توضيحية • توقيت الرياض", w - 25, 16, 10)
        pdf.save()
        return out.getvalue()

    keys = columns(group)
    weights = (
        [5, 9, 10, 9, 20, 5, 5, 7, 7, 7, 9]
        if group == "missions"
        else [17, 22, 20, 15, 15]
    )
    right = w - 28
    widths = [(w - 110) * v / sum(weights) for v in weights]
    measured = [
        r
        for r in rows
        if not r.get("parent") and isinstance(r["distance"], (int, float))
    ]
    total = sum(r["distance"] for r in measured)

    def header():
        pdf.setFillColor(colors.HexColor("#194C50"))
        pdf.rect(0, h - 105, w, 105, fill=1, stroke=0)
        write(title(group, day), right, h - 40, 19, "#ffffff")
        write(
            f"{len(rows)} سجل    |    {total:g} كم مسجلة    |    {len(measured)} سجل بمسافة مكتملة",
            right,
            h - 76,
            12,
            "#edf5ee",
        )
        pdf.setFillColor(colors.HexColor("#ecf3f1"))
        pdf.rect(25, h - 148, w - 50, 35, fill=1, stroke=0)
        x = right
        for k, b in zip(keys, widths):
            write(LABELS[k], x, h - 135, 9)
            x -= b
        write("الشكل", x, h - 135, 9)
        return h - 154

    prepared = []
    for r in rows:
        cells = []
        for k, b in zip(keys, widths):
            val = r.get(k)
            s = "—" if val is None or val == "" else str(val)
            lines = []
            line = ""
            for chunk in wrap(s, width=max(3, int(b / 4.5)), break_long_words=True) or [
                "—"
            ]:
                for word in chunk.split():
                    test = (line + " " + word).strip()
                    if line and pdfmetrics.stringWidth(visual(test), font, 9) > b - 7:
                        lines.append(line)
                        line = ""
                    line = (line + " " + word).strip()
            if line:
                lines.append(line)
            cells.append(lines)
        rh = max(31, 12 * max(map(len, cells)) + 11)
        prepared.append((r, cells, rh))
    # Keep the daily register on one sheet. For busy days extend the canvas,
    # preserving every order and the same readable Arabic type size.
    h = max(h, 184 + sum(row_height for _, _, row_height in prepared))
    pdf.setPageSize((w, h))
    y = header()
    if not rows:
        write("لا توجد سجلات لهذا اليوم ضمن الاختيار.", right, y - 39)
    for i, (r, cells, rh) in enumerate(prepared):
        if i % 2 == 0:
            pdf.setFillColor(colors.HexColor("#f7faf9"))
            pdf.rect(25, y - rh, w - 50, rh, fill=1, stroke=0)
        x = right
        for lines, b in zip(cells, widths):
            for j, line in enumerate(lines):
                write(line, x, y - 14 - 12 * j, 9)
            x -= b
        if isinstance(r.get("art"), int):
            art = ASSETS / "fleet-models" / f"{r['art']}.jpg"
            if art.exists():
                pdf.drawImage(
                    str(art),
                    28,
                    y - rh + 3,
                    width=68,
                    height=min(rh - 5, 38),
                    preserveAspectRatio=True,
                    anchor="c",
                )
        y -= rh
    write("الصور توضيحية • توقيت الرياض", right, 16, 8)
    pdf.save()
    return out.getvalue()


def report_data(c, brand, day):
    saved = c.execute(
        "SELECT snapshot FROM fleet_morning_reports WHERE brand_id=%s AND day=%s",
        (brand, day),
    ).fetchone()
    return (
        saved["snapshot"]
        if saved
        else snapshot(c, brand, day, service_hours.now_riyadh())
    )


@router.get("/reports/{group}")
def report(
    group: Literal["missions", "delivery"],
    day: date,
    fmt: Literal["json", "pdf", "xlsx"] = "json",
    employee: str = "",
    vehicle: str = "",
    kind: str = "",
    status: str = "",
    user=Depends(auth.current_user),
):
    with db.tx() as c:
        actor(c, user, lead=True)
        data = report_data(c, user["brand_id"], day)
    all_rows = data[group]
    rows = [
        r
        for r in all_rows
        if (not employee or r["employee_id"] == employee)
        and (not vehicle or vehicle in r["vehicle_ids"])
        and (not kind or kind == r["kind"])
        and (not status or r["code"] == status)
    ]
    if fmt == "json":
        return {
            "rows": rows,
            "as_of": data["as_of"],
            "title": title(group, day),
            "columns": {k: LABELS[k] for k in columns(group, True)},
            "employees": {
                r["employee_id"]: r["employee"] for r in all_rows if r["employee_id"]
            },
            "vehicles": {
                v: p
                for r in all_rows
                for v, p in zip(r["vehicle_ids"], r["plate"].split(" / "))
            },
        }
    keys = columns(group, True)
    if fmt == "pdf":
        content = make_pdf(rows, group, day)
        mime = "application/pdf"
    else:
        content = accounting_export.xlsx(
            [
                [LABELS[k] for k in keys],
                *[
                    [r.get(k) if r.get(k) is not None else "" for k in keys]
                    for r in rows
                ],
            ],
            keys,
            [title(group, day)],
            "ar",
        )
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content,
        media_type=mime,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename=fleet-{day}.{fmt}; filename*=UTF-8''"
            + quote(title(group, day) + "." + fmt),
        },
    )


def recipient(c, brand):
    return c.execute(
        """SELECT lower(trim(n.recipient_email)) address FROM manager_notification_subscriptions n
        JOIN fleet_mission_staff s ON s.brand_id=n.brand_id AND s.user_id=n.user_id AND s.profile='director'
        JOIN users u ON u.id=n.user_id AND u.active AND u.role<>'customer' JOIN brands b ON b.id=n.brand_id AND b.active
        WHERE n.brand_id=%s AND n.enabled AND n.daily_reports AND lower(trim(n.recipient_email))=%s""",
        (brand, mission_policy.config().get(brand, {}).get("report_email", "").lower()),
    ).fetchone()


def tick():
    now = service_hours.now_riyadh()
    if (now.hour, now.minute) < (7, 50):
        return
    day = now.date() - timedelta(days=1)
    for brand in mission_policy.config():
        with db.tx() as c:
            if not c.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) ok",
                ("fleet-morning:" + brand,),
            ).fetchone()["ok"]:
                continue
            row = c.execute(
                "SELECT snapshot,mail_id FROM fleet_morning_reports WHERE brand_id=%s AND day=%s",
                (brand, day),
            ).fetchone()
            if row and row["mail_id"]:
                continue
            if not row:
                data = jsonable_encoder(snapshot(c, brand, day, now))
                c.execute(
                    "INSERT INTO fleet_morning_reports(brand_id,day,snapshot) VALUES(%s,%s,%s)",
                    (brand, day, db.json(data)),
                )
            else:
                data = row["snapshot"]
            dest = recipient(c, brand)
            if dest and email_delivery.configuration()["configured"]:
                attachments = [
                    dict(
                        filename=title(g, day) + ".pdf",
                        content=base64.b64encode(make_pdf(data[g], g, day)).decode(),
                    )
                    for g in TITLES
                ]
                mail = email_delivery.enqueue_mail(
                    c,
                    brand_id=brand,
                    recipient=dest["address"],
                    kind="fleet_daily",
                    dedupe_key="fleet-morning:" + str(day),
                    payload=dict(
                        subject=f"تقرير السيارات اليومي - {DAYS[day.weekday()]} {day}",
                        text="مرفق تقرير أوامر حركة السيارات وتقرير شفتات توصيل الطلبات.",
                        attachments=attachments,
                    ),
                )
                c.execute(
                    "UPDATE fleet_morning_reports SET mail_id=%s WHERE brand_id=%s AND day=%s",
                    (mail, brand, day),
                )


async def poll_loop():
    last_sync = None
    while True:
        try:
            today = service_hours.now_riyadh().replace(
                minute=0, second=0, microsecond=0
            )
            if last_sync != today:

                def sync():
                    with db.tx() as c:
                        mission_policy.sync(c)

                await asyncio.to_thread(sync)
                last_sync = today
            await asyncio.to_thread(tick)
        except Exception as exc:
            logging.getLogger("kps_order").error(
                "fleet_morning_worker_failure type=%s", type(exc).__name__
            )
        await asyncio.sleep(15)

