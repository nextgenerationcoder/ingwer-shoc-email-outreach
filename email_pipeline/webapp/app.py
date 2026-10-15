"""
Web UI for the outreach pipeline: connect mailbox/API credentials, edit the
AI prompt, run test generations (and optional test sends) before anything
goes to real contacts, and see/manage the contacts list and daily runs.

This process also runs the daily planner and per-minute send worker in
the background (see scheduler.py) -- as long as this webapp is deployed
and running, the campaign runs itself with no separate cron needed.

Run with:
    python -m email_pipeline.webapp.app
then open http://localhost:5000
"""
import io
import json
import os
import csv as csv_module
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

from .. import config, db
from ..assign import assign_mailbox
from ..generate import generate_from_fields, DEFAULT_PROMPT_TEMPLATE
from ..sender import send_email
from ..plan_daily import plan_today
from ..worker import run_due_sends
from ..form_crawler import crawl_target_headless
from .scheduler import start_scheduler

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-secret-change-me")

# Started once per process at import time. The dev server below runs
# with use_reloader=False specifically so this only ever starts once;
# in production, run a single worker process (e.g. `gunicorn -w 1`) so
# these jobs don't double-run.
start_scheduler()


@app.before_request
def ensure_db():
    db.init_db()


@app.after_request
def add_cors_headers(response):
    # the Chrome extension's popup/content scripts call /api/* from a
    # chrome-extension:// origin, which needs an explicit CORS allow
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def api_options(_any):
    return "", 204


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-API-Key") != config.get_extension_api_key():
            return jsonify({"error": "invalid or missing X-API-Key"}), 401
        return fn(*args, **kwargs)

    return wrapper


@app.route("/api/forms/pending")
@require_api_key
def api_forms_pending():
    limit = min(int(request.args.get("limit", 20)), 100)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, url, name, company FROM form_targets "
            "WHERE status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/forms/by_url")
@require_api_key
def api_forms_by_url():
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "missing url query param"}), 400
    # match by domain, not exact URL, since the extension reports the
    # page you're actually on (e.g. /pages/contact) which may differ
    # from the stored target URL (e.g. the shop's homepage)
    try:
        domain = url.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
    except Exception:
        domain = url
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, url, name, company, status FROM form_targets "
            "WHERE url LIKE ? ORDER BY id",
            (f"%{domain}%",),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/forms/<int:target_id>/generate", methods=["POST"])
@require_api_key
def api_generate(target_id):
    with db.connection() as conn:
        target = conn.execute(
            "SELECT * FROM form_targets WHERE id = ?", (target_id,)
        ).fetchone()
    if not target:
        return jsonify({"error": "not found"}), 404

    subject, body = generate_from_fields(
        name=target["name"],
        company=target["company"],
        sender_company=config.get_sender_company_name(),
        product_description=config.get_product_description(),
        api_key=config.get_deepseek_api_key(),
        prompt_template=config.get_prompt_template_override(),
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE form_targets SET subject = ?, body = ?, updated_at = datetime('now') WHERE id = ?",
            (subject, body, target_id),
        )
    return jsonify({
        "id": target_id,
        "subject": subject,
        "body": body,
        "sender_name": config.get_sender_company_name(),
        "sender_email": config.get_contact_reply_email(),
        "sender_phone": config.get_contact_phone(),
    })


@app.route("/api/forms/<int:target_id>/submitted", methods=["POST"])
@require_api_key
def api_mark_submitted(target_id):
    with db.connection() as conn:
        conn.execute(
            "UPDATE form_targets SET status = 'submitted', updated_at = datetime('now') WHERE id = ?",
            (target_id,),
        )
    return jsonify({"ok": True})


@app.route("/api/forms/<int:target_id>/skip", methods=["POST"])
@require_api_key
def api_mark_skip(target_id):
    reason = (request.get_json(silent=True) or {}).get("reason", "skipped from extension")
    with db.connection() as conn:
        conn.execute(
            "UPDATE form_targets SET status = 'failed', error = ?, updated_at = datetime('now') WHERE id = ?",
            (reason, target_id),
        )
    return jsonify({"ok": True})


@app.route("/api/forms", methods=["POST"])
@require_api_key
def api_add_target():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "missing url"}), 400
    with db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO form_targets (url, name, company) VALUES (?, ?, ?)",
            (url, data.get("name", "").strip(), data.get("company", "").strip()),
        )
        target_id = cur.lastrowid
    return jsonify({"id": target_id})


@app.route("/")
def dashboard():
    with db.connection() as conn:
        counts = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) c FROM contacts GROUP BY status"
            ).fetchall()
        }
        recent = conn.execute(
            "SELECT sq.*, c.email FROM send_queue sq "
            "JOIN contacts c ON c.id = sq.contact_id "
            "ORDER BY sq.id DESC LIMIT 20"
        ).fetchall()
    mailboxes = config.load_mailboxes()
    return render_template(
        "dashboard.html",
        active="dashboard",
        counts=counts,
        recent=recent,
        mailbox_count=len(mailboxes),
        has_api_key=bool(config.get_deepseek_api_key()),
    )


@app.route("/run/plan", methods=["POST"])
def run_plan():
    plan_today()
    flash("Today's batch has been planned (or was already planned).")
    return redirect(url_for("dashboard"))


@app.route("/run/worker", methods=["POST"])
def run_worker():
    run_due_sends()
    flash("Checked for due sends and sent whatever was ready.")
    return redirect(url_for("dashboard"))


@app.route("/contacts")
def contacts():
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM contacts ORDER BY id DESC LIMIT 300"
        ).fetchall()
    return render_template("contacts.html", active="contacts", contacts=rows)


@app.route("/contacts/import", methods=["POST"])
def import_contacts_route():
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("contacts"))

    text = file.read().decode("utf-8", errors="ignore")
    reader = csv_module.DictReader(io.StringIO(text))
    added, skipped = 0, 0
    with db.connection() as conn:
        for row in reader:
            email = (row.get("email") or "").strip().lower()
            if not email:
                continue
            try:
                conn.execute(
                    "INSERT INTO contacts (email, name, company, source, "
                    "assigned_mailbox_id) VALUES (?, ?, ?, ?, ?)",
                    (
                        email,
                        row.get("name"),
                        row.get("company"),
                        row.get("source"),
                        assign_mailbox(email),
                    ),
                )
                added += 1
            except Exception:
                skipped += 1
    flash(f"Imported {added} new contacts, skipped {skipped} duplicates.")
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:contact_id>/unsubscribe", methods=["POST"])
def unsubscribe_route(contact_id):
    with db.connection() as conn:
        conn.execute(
            "UPDATE contacts SET status = 'unsubscribed' WHERE id = ?",
            (contact_id,),
        )
    flash("Contact marked unsubscribed.")
    return redirect(url_for("contacts"))


@app.route("/forms")
def forms_page():
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM form_targets ORDER BY id DESC LIMIT 300"
        ).fetchall()
        counts = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) c FROM form_targets GROUP BY status"
            ).fetchall()
        }
    targets = []
    for r in rows:
        t = dict(r)
        try:
            t["unmatched_fields"] = json.loads(t.get("unmatched_fields") or "[]")
        except (TypeError, ValueError):
            t["unmatched_fields"] = []
        targets.append(t)
    return render_template("forms.html", active="forms", targets=targets, counts=counts)


@app.route("/forms/add", methods=["POST"])
def add_single_form_route():
    url = request.form.get("url", "").strip()
    if not url:
        flash("Enter a URL.", "error")
        return redirect(url_for("forms_page"))
    with db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO form_targets (url, name, company) VALUES (?, ?, ?)",
            (url, request.form.get("name", "").strip(), request.form.get("company", "").strip()),
        )
        target_id = cur.lastrowid
    flash("Added -- click Preview to fill and review it.")
    return redirect(url_for("forms_page"))


@app.route("/forms/import", methods=["POST"])
def import_forms_route():
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("forms_page"))

    text = file.read().decode("utf-8", errors="ignore")
    reader = csv_module.DictReader(io.StringIO(text))
    added = 0
    with db.connection() as conn:
        for row in reader:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            conn.execute(
                "INSERT INTO form_targets (url, name, company) VALUES (?, ?, ?)",
                (url, row.get("name"), row.get("company")),
            )
            added += 1
    flash(f"Added {added} shop URL(s).")
    return redirect(url_for("forms_page"))


@app.route("/forms/run", methods=["POST"])
def run_forms_route():
    from playwright.sync_api import sync_playwright

    sender_company = config.get_sender_company_name()
    product_description = config.get_product_description()
    api_key = config.get_deepseek_api_key()
    reply_email = config.get_contact_reply_email()
    reply_phone = config.get_contact_phone()

    with db.connection() as conn:
        pending = conn.execute(
            "SELECT * FROM form_targets WHERE status = 'pending' LIMIT 25"
        ).fetchall()

    if not pending:
        flash("No pending shop URLs to crawl.")
        return redirect(url_for("forms_page"))

    submitted, needs_manual, failed = 0, 0, 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for target in pending:
            subject, body = generate_from_fields(
                name=target["name"],
                company=target["company"],
                sender_company=sender_company,
                product_description=product_description,
                api_key=api_key,
                prompt_template=config.get_prompt_template_override(),
            )
            result = crawl_target_headless(
                browser,
                url=target["url"],
                name=target["name"],
                company=target["company"],
                sender_name=sender_company,
                sender_email=reply_email,
                sender_phone=reply_phone,
                subject=subject,
                body=body,
            )
            with db.connection() as conn:
                conn.execute(
                    "UPDATE form_targets SET status = ?, contact_url = ?, "
                    "subject = ?, body = ?, error = ?, unmatched_fields = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (
                        result["status"],
                        result["contact_url"],
                        subject,
                        body,
                        result["error"],
                        json.dumps(result.get("unmatched_fields", [])),
                        target["id"],
                    ),
                )
            submitted += result["status"] == "submitted"
            needs_manual += result["status"] == "needs_manual"
            failed += result["status"] == "failed"
        browser.close()

    flash(
        f"Crawled {len(pending)}: {submitted} submitted, "
        f"{needs_manual} need your manual input, {failed} failed."
    )
    return redirect(url_for("forms_page"))


@app.route("/forms/<int:target_id>/preview", methods=["POST"])
def preview_form_route(target_id):
    from playwright.sync_api import sync_playwright

    with db.connection() as conn:
        target = conn.execute(
            "SELECT * FROM form_targets WHERE id = ?", (target_id,)
        ).fetchone()
    if not target:
        flash("Target not found.", "error")
        return redirect(url_for("forms_page"))

    subject, body = generate_from_fields(
        name=target["name"],
        company=target["company"],
        sender_company=config.get_sender_company_name(),
        product_description=config.get_product_description(),
        api_key=config.get_deepseek_api_key(),
        prompt_template=config.get_prompt_template_override(),
    )

    screenshots_dir = os.path.join(os.path.dirname(__file__), "static", "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    screenshot_filename = f"{target_id}.png"
    screenshot_path = os.path.join(screenshots_dir, screenshot_filename)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        result = crawl_target_headless(
            browser,
            url=target["url"],
            name=target["name"],
            company=target["company"],
            sender_name=config.get_sender_company_name(),
            sender_email=config.get_contact_reply_email(),
            sender_phone=config.get_contact_phone(),
            subject=subject,
            body=body,
            dry_run=True,
            screenshot_path=screenshot_path,
        )
        browser.close()

    with db.connection() as conn:
        conn.execute(
            "UPDATE form_targets SET status = ?, contact_url = ?, subject = ?, "
            "body = ?, screenshot = ?, error = ?, unmatched_fields = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (
                result["status"],
                result["contact_url"],
                subject,
                body,
                screenshot_filename if result["status"] == "pending_review" else None,
                result["error"],
                json.dumps(result.get("unmatched_fields", [])),
                target_id,
            ),
        )

    if result["status"] == "pending_review":
        flash("Form filled -- review it below and approve to submit.")
    elif result["status"] == "needs_manual":
        flash("CAPTCHA found -- open the link to finish this one by hand.")
    else:
        flash(f"Couldn't fill a form: {result['error']}", "error")
    return redirect(url_for("forms_page"))


@app.route("/forms/<int:target_id>/approve", methods=["POST"])
def approve_form_route(target_id):
    from playwright.sync_api import sync_playwright

    with db.connection() as conn:
        target = conn.execute(
            "SELECT * FROM form_targets WHERE id = ?", (target_id,)
        ).fetchone()
    if not target or target["status"] != "pending_review":
        flash("Nothing pending approval for that target.", "error")
        return redirect(url_for("forms_page"))

    # re-fill with the SAME subject/body shown in the preview, then submit
    # for real -- the browser tab from the preview can't be kept open
    # across separate web requests, so the form is filled fresh here.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        result = crawl_target_headless(
            browser,
            url=target["url"],
            name=target["name"],
            company=target["company"],
            sender_name=config.get_sender_company_name(),
            sender_email=config.get_contact_reply_email(),
            sender_phone=config.get_contact_phone(),
            subject=target["subject"],
            body=target["body"],
            dry_run=False,
        )
        browser.close()

    with db.connection() as conn:
        conn.execute(
            "UPDATE form_targets SET status = ?, error = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (result["status"], result["error"], target_id),
        )

    flash("Submitted." if result["status"] == "submitted" else f"Submit failed: {result['error']}")
    return redirect(url_for("forms_page"))


@app.route("/forms/<int:target_id>/discard", methods=["POST"])
def discard_form_route(target_id):
    with db.connection() as conn:
        conn.execute(
            "UPDATE form_targets SET status = 'pending', screenshot = NULL "
            "WHERE id = ? AND status = 'pending_review'",
            (target_id,),
        )
    flash("Discarded -- back to pending.")
    return redirect(url_for("forms_page"))


def _delete_form_target(conn, target_id):
    row = conn.execute("SELECT screenshot FROM form_targets WHERE id = ?", (target_id,)).fetchone()
    if row and row["screenshot"]:
        screenshot_path = os.path.join(
            os.path.dirname(__file__), "static", "screenshots", row["screenshot"]
        )
        try:
            os.remove(screenshot_path)
        except OSError:
            pass
    conn.execute("DELETE FROM form_targets WHERE id = ?", (target_id,))


@app.route("/forms/<int:target_id>/delete", methods=["POST"])
def delete_form_route(target_id):
    with db.connection() as conn:
        _delete_form_target(conn, target_id)
    flash("Removed from the list.")
    return redirect(url_for("forms_page"))


@app.route("/forms/delete_pending", methods=["POST"])
def delete_pending_forms_route():
    with db.connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM form_targets WHERE status IN ('pending', 'failed')"
        ).fetchall()]
        for target_id in ids:
            _delete_form_target(conn, target_id)
    flash(f"Removed {len(ids)} pending target(s).")
    return redirect(url_for("forms_page"))


@app.route("/test", methods=["GET", "POST"])
def test_page():
    result = None
    form = {"name": "", "company": "", "to_email": ""}
    if request.method == "POST":
        form["name"] = request.form.get("name", "")
        form["company"] = request.form.get("company", "")
        form["to_email"] = request.form.get("to_email", "")

        subject, body = generate_from_fields(
            name=form["name"],
            company=form["company"],
            sender_company=config.get_sender_company_name(),
            product_description=config.get_product_description(),
            api_key=config.get_deepseek_api_key(),
            prompt_template=config.get_prompt_template_override(),
        )
        result = {"subject": subject, "body": body}

        if request.form.get("action") == "send_test" and form["to_email"]:
            mailboxes = config.load_mailboxes()
            if not mailboxes:
                flash("No mailboxes configured yet -- add one in Settings first.", "error")
            else:
                try:
                    send_email(mailboxes[0], form["to_email"], subject, body)
                    flash(f"Test email sent to {form['to_email']} via mailbox 1.")
                except Exception as exc:
                    flash(f"Send failed: {exc}", "error")

    return render_template("test.html", active="test", form=form, result=result)


@app.route("/prompt", methods=["GET", "POST"])
def prompt_page():
    if request.method == "POST":
        if request.form.get("action") == "reset":
            db.set_setting("prompt_template", "")
            flash("Prompt reset to default.")
        else:
            db.set_setting("prompt_template", request.form.get("prompt", ""))
            flash("Prompt saved.")
        return redirect(url_for("prompt_page"))

    current = config.get_prompt_template_override() or DEFAULT_PROMPT_TEMPLATE
    return render_template(
        "prompt.html",
        active="prompt",
        prompt=current,
        is_default=not config.get_prompt_template_override(),
        has_api_key=bool(config.get_deepseek_api_key()),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        db.set_setting("deepseek_api_key", request.form.get("deepseek_api_key", "").strip())
        db.set_setting("sender_company_name", request.form.get("sender_company_name", "").strip())
        db.set_setting("product_description", request.form.get("product_description", "").strip())
        db.set_setting("contact_reply_email", request.form.get("contact_reply_email", "").strip())
        db.set_setting("contact_phone", request.form.get("contact_phone", "").strip())
        db.set_setting("daily_min_per_mailbox", request.form.get("daily_min_per_mailbox", "10").strip())
        db.set_setting("daily_max_per_mailbox", request.form.get("daily_max_per_mailbox", "15").strip())
        db.set_setting("send_window_start_hour", request.form.get("send_window_start_hour", "8").strip())
        db.set_setting("send_window_end_hour", request.form.get("send_window_end_hour", "21").strip())

        mailboxes = []
        for n in range(1, 5):
            host = request.form.get(f"mb{n}_host", "").strip()
            if not host:
                continue
            mailboxes.append(
                config.Mailbox(
                    id=n,
                    smtp_host=host,
                    smtp_port=int(request.form.get(f"mb{n}_port", "587") or 587),
                    username=request.form.get(f"mb{n}_username", "").strip(),
                    password=request.form.get(f"mb{n}_password", "").strip(),
                    from_name=request.form.get(f"mb{n}_from_name", "").strip(),
                )
            )
        config.save_mailboxes(mailboxes)
        flash("Settings saved.")
        return redirect(url_for("settings_page"))

    mailboxes = {m.id: m for m in config.load_mailboxes()}
    return render_template(
        "settings.html",
        active="settings",
        mailboxes=mailboxes,
        deepseek_api_key=config.get_deepseek_api_key() or "",
        sender_company_name=config.get_sender_company_name(),
        product_description=config.get_product_description(),
        contact_reply_email=config.get_contact_reply_email(),
        contact_phone=config.get_contact_phone(),
        daily_min=config.get_daily_min_per_mailbox(),
        daily_max=config.get_daily_max_per_mailbox(),
        window_start=config.get_send_window_start_hour(),
        window_end=config.get_send_window_end_hour(),
        extension_api_key=config.get_extension_api_key(),
    )


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=5000)
