"""HTTP routes. Thin wrappers over LabService; all pages are server-rendered."""

from __future__ import annotations

from datetime import timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .models import ProjectType, RequestStatus, Role, User
from .policy import WEEKLY_CAP_HOURS
from .scheduling import CLOSE_HOUR, MAX_BOOKING_HOURS, OPEN_HOUR
from .security import client_key, trusted_email
from .services import LabService, NotFound, PermissionDenied
from .validation import (
    MAX_QUERY,
    MAX_TEXT,
    ValidationError,
    clean_text,
    parse_booking_form,
    parse_request_form,
    valid_id,
)

bp = Blueprint("main", __name__)
DEFAULT_USER = "s_ananya"


def svc() -> LabService:
    return current_app.extensions["lablink"]


def demo_mode() -> bool:
    return bool(current_app.config.get("DEMO_MODE", True))


def signed_in_user() -> User | None:
    """The user named by the identity proxy (DEMO_MODE off), matched by email."""
    email = trusted_email()
    if not email:
        return None
    return next((u for u in svc().repo.list_users() if u.email.lower() == email), None)


def current_user() -> User:
    if not demo_mode():
        user = signed_in_user()
        if user is None:  # require_sign_in normally stops this earlier
            abort(401)
        return user
    user = svc().repo.get_user(session.get("user_id", DEFAULT_USER)) or svc().repo.get_user(DEFAULT_USER)
    if user is None:  # demo data missing entirely
        abort(503, description="Demo data is not loaded yet. Try again in a moment.")
    return user


@bp.before_app_request
def require_sign_in():
    """Outside demo mode every page needs a recognised signed-in user."""
    if demo_mode() or request.endpoint in ("main.healthz", "static"):
        return None
    if signed_in_user() is None:
        abort(401)
    return None


def ai_allowed() -> bool:
    """Per-browser limit (keyed on the signed session, falling back to IP, since many
    users can share one proxy address) plus a global cap that protects the API quota."""
    return (
        current_app.extensions["ai_limiter"].allow(client_key())
        and current_app.extensions["ai_global_limiter"].allow("global")
    )


def handles_errors(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except NotFound:
            abort(404)
        except PermissionDenied as exc:
            abort(403, description=str(exc))

    return wrapper


def roles(*allowed: Role):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if current_user().role not in allowed:
                abort(403, description="Switch to a role that can use this page.")
            return view(*args, **kwargs)

        return wrapper

    return decorator


@bp.app_context_processor
def inject_globals():
    # Never abort here: this also runs for the sign-in page, where nobody is signed in.
    me = current_user() if demo_mode() else signed_in_user()
    return {
        "me": me,
        "all_users": sorted(svc().repo.list_users(), key=lambda u: (u.role, u.name)) if demo_mode() else [],
        "ai_enabled": svc().ai.enabled,
        "demo_mode": demo_mode(),
        "weekly_cap": WEEKLY_CAP_HOURS,
        "hours_this_week": svc().hours_this_week(me) if me and me.role == Role.STUDENT else None,
        "Role": Role,
    }


@bp.get("/healthz")
def healthz():
    return {"status": "ok"}


@bp.post("/switch-user")
def switch_user():
    if not demo_mode():
        abort(404)
    user_id = valid_id(request.form.get("user_id"))
    chosen = svc().repo.get_user(user_id) if user_id else None
    if chosen is not None:
        session["user_id"] = chosen.id
        flash(f"Now viewing as {chosen.name}.")
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("main.discover"))


@bp.post("/demo/reset")
@handles_errors
@roles(Role.ADMIN)
def reset_demo():
    if not demo_mode():
        abort(404)
    svc().reset_demo(current_user())
    session["user_id"] = "a_lab"
    flash("Demo data reset to its starting state.", "success")
    return redirect(url_for("main.allocate"))


# Discovery -----------------------------------------------------------------
@bp.get("/")
def discover():
    query = clean_text(request.args.get("q"), MAX_QUERY)
    department = clean_text(request.args.get("department"), 60)
    category = clean_text(request.args.get("category"), 60)
    catalogue = svc().repo.list_equipment()
    if query and not ai_allowed():
        abort(429, description="Too many AI searches. Wait a minute and try again.")
    results, source = svc().search(query, department, category)
    return render_template(
        "discover.html",
        query=query,
        department=department,
        category=category,
        departments=sorted({e.department for e in catalogue}),
        categories=sorted({e.category for e in catalogue}),
        results=[(eq, reason, svc().free_now(eq), tier) for eq, reason, tier in results],
        result_source=source,
    )


@bp.route("/equipment/<equipment_id>", methods=["GET"])
@handles_errors
def equipment_detail(equipment_id: str, booking_result=None, checklist=None, form_values=None):
    eq = svc().require_equipment(equipment_id)
    me = current_user()
    today = svc().now().date()
    return render_template(
        "equipment.html",
        eq=eq,
        grid=svc().week_grid(eq, today),
        open_hour=OPEN_HOUR,
        close_hour=CLOSE_HOUR,
        max_hours=MAX_BOOKING_HOURS,
        bookable=svc().bookable_requests(me, eq.id) if me.role == Role.STUDENT else [],
        result=booking_result,
        checklist=checklist,
        form=form_values or {"date": (today + timedelta(days=1)).isoformat(), "start_hour": "10", "hours": "2"},
        today=today,
    )


@bp.post("/equipment/<equipment_id>/book")
@handles_errors
@roles(Role.STUDENT)
def book(equipment_id: str):
    try:
        form = parse_booking_form(request.form, svc().now())
    except ValidationError as exc:
        for e in exc.errors:
            flash(e, "error")
        return equipment_detail(equipment_id, form_values=request.form), 400
    result = svc().book(current_user(), equipment_id, form, join_waitlist=request.form.get("waitlist") == "1")
    if result.status in ("confirmed", "waitlisted"):
        flash(result.message, "success")
        return redirect(url_for("main.my_bookings"))
    return equipment_detail(equipment_id, booking_result=result, form_values=request.form), 409


@bp.post("/equipment/<equipment_id>/checklist")
@handles_errors
def checklist(equipment_id: str):
    eq = svc().require_equipment(equipment_id)
    project = clean_text(request.form.get("project"), MAX_TEXT) or "general use"
    if not ai_allowed():
        abort(429, description="Too many AI requests. Wait a minute and try again.")
    steps, used_ai = svc().ai.safety_checklist(eq, project)
    return equipment_detail(equipment_id, checklist={"steps": steps, "ai": used_ai, "project": project})


# Project requests ---------------------------------------------------------
def _request_page(**extra):
    me = current_user()
    requests_ = svc().visible_requests(me)
    briefs = {}
    if me.role in (Role.FACULTY, Role.ADMIN):
        briefs = {r.id: svc().brief_for(r) for r in requests_ if r.status == RequestStatus.PENDING}
    context = dict(
        requests=requests_,
        briefs=briefs,
        equipment=svc().equipment_map(),
        users=svc().user_map(),
        faculty=svc().faculty(),
        project_types=list(ProjectType),
        today=svc().now().date(),
        draft={},
        suggestions={},
        suggested_source="",
        suggest_ran=False,
    )
    context.update(extra)
    return render_template("requests.html", **context)


@bp.get("/requests")
def requests_page():
    return _request_page()


@bp.post("/requests/suggest")
@roles(Role.STUDENT)
def suggest_equipment():
    description = clean_text(request.form.get("description"), MAX_TEXT)
    draft = {k: clean_text(request.form.get(k), MAX_TEXT) for k in ("project_title", "description", "deadline", "guide_id", "project_type")}
    if len(description) < 20:
        flash("Describe the project in at least 20 characters to get suggestions.", "error")
        return _request_page(draft=draft), 400
    if not ai_allowed():
        abort(429, description="Too many AI requests. Wait a minute and try again.")
    suggestions, source = svc().suggest_needs(description, current_user())
    return _request_page(
        draft=draft, suggestions={m.equipment_id: m for m in suggestions}, suggested_source=source, suggest_ran=True
    )


@bp.post("/requests")
@handles_errors
@roles(Role.STUDENT)
def create_request():
    service = svc()
    try:
        form = parse_request_form(
            request.form,
            service.now().date(),
            set(service.equipment_map()),
            {u.id for u in service.faculty()},
        )
    except ValidationError as exc:
        for e in exc.errors:
            flash(e, "error")
        draft = {k: clean_text(request.form.get(k), MAX_TEXT) for k in ("project_title", "description", "deadline", "guide_id", "project_type")}
        return _request_page(draft=draft), 400
    service.create_request(current_user(), form)
    flash("Request submitted to your faculty guide.", "success")
    return redirect(url_for("main.requests_page"))


@bp.post("/requests/<request_id>/decide")
@handles_errors
@roles(Role.FACULTY, Role.ADMIN)
def decide(request_id: str):
    decision = request.form.get("decision")
    if decision not in ("approve", "reject"):
        abort(400)
    note = clean_text(request.form.get("note"), 300)
    svc().decide_request(current_user(), request_id, decision == "approve", note)
    flash(f"Request {'approved' if decision == 'approve' else 'rejected'}.", "success")
    return redirect(url_for("main.requests_page"))


@bp.get("/requests/<request_id>/plan")
@handles_errors
@roles(Role.STUDENT)
def plan_project_page(request_id: str):
    request_, items = svc().plan_for(current_user(), request_id)
    return render_template(
        "plan.html",
        project=request_,
        items=items,
        equipment=svc().equipment_map(),
        sessions=sum(len(i.sessions) for i in items),
        weekly_cap=WEEKLY_CAP_HOURS,
    )


@bp.post("/requests/<request_id>/plan/book")
@handles_errors
@roles(Role.STUDENT)
def book_plan(request_id: str):
    booked, failed = svc().book_plan(current_user(), request_id)
    if booked and not failed:
        flash(f"Booked all {booked} session{'s' if booked != 1 else ''}.", "success")
    elif booked:
        flash(f"Booked {booked} session(s). {failed} could not be booked because the slot was taken; plan again for new times.", "success")
    else:
        flash("Nothing was booked: those slots are no longer free. Here is a fresh plan.", "error")
        return redirect(url_for("main.plan_project_page", request_id=request_id))
    return redirect(url_for("main.my_bookings"))


# Bookings -----------------------------------------------------------------
@bp.get("/bookings")
def my_bookings():
    me = current_user()
    bookings = svc().repo.bookings_for_user(me.id) if me.role == Role.STUDENT else svc().repo.list_bookings()
    now = svc().now()
    upcoming = [b for b in bookings if b.end >= now and b.status != "cancelled"]
    return render_template(
        "bookings.html",
        bookings=upcoming,
        equipment=svc().equipment_map(),
        users=svc().user_map(),
        requests={r.id: r for r in svc().repo.list_requests()},
    )


@bp.post("/bookings/<booking_id>/cancel")
@handles_errors
def cancel_booking(booking_id: str):
    svc().cancel_booking(current_user(), booking_id)
    flash("Booking cancelled.", "success")
    return redirect(url_for("main.my_bookings"))


# Fair allocation ----------------------------------------------------------
@bp.get("/allocate")
@handles_errors
@roles(Role.FACULTY, Role.ADMIN)
def allocate():
    contested = svc().contested_equipment()
    selected = valid_id(request.args.get("equipment")) or (contested[0][0].id if contested else None)
    decisions = []
    if selected:
        if not ai_allowed():
            abort(429, description="Too many AI requests. Wait a minute and try again.")
        decisions = svc().plan(current_user(), selected)
    return render_template(
        "allocate.html",
        contested=contested,
        selected=svc().repo.get_equipment(selected) if selected else None,
        decisions=decisions,
        equipment=svc().equipment_map(),
        users=svc().user_map(),
    )


@bp.post("/allocate/<equipment_id>/apply")
@handles_errors
@roles(Role.FACULTY, Role.ADMIN)
def apply_allocation(equipment_id: str):
    applied = svc().apply_plan(current_user(), equipment_id)
    flash(f"Applied fair allocation: {applied} booking(s) confirmed. Students see the result in their bookings.", "success")
    return redirect(url_for("main.allocate"))


# Utilisation --------------------------------------------------------------
@bp.get("/insights")
@roles(Role.ADMIN, Role.FACULTY)
def insights():
    stats = svc().utilisation_stats()
    tips, used_ai = svc().ai.utilisation_insights(stats) if ai_allowed() else ([], False)
    return render_template("insights.html", stats=stats, tips=tips, used_ai=used_ai)
