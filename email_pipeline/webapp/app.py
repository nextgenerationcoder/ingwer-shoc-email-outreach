"""
Web UI for the outreach pipeline: connect mailbox/API credentials, edit the
AI prompt, run test generations (and optional test sends) before anything
goes to real contacts, and see/manage the contacts list and daily runs.

Run with:
    python -m email_pipeline.webapp.app
then open http://localhost:5000
"""
import io
import csv as csv_module

from flask import Flask, render_template, request, redirect, url_for, flash

from .. import config, db
from ..assign import assign_mailbox
from ..generate import generate_from_fields, DEFAULT_PROMPT_TEMPLATE
from ..sender import send_email
from ..plan_daily import plan_today
from ..worker import run_due_sends

app = Flask(__name__)
app.secret_key = "dev-only-secret-change-me"


@app.before_request
def ensure_db():
    db.init_db()


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
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        db.set_setting("deepseek_api_key", request.form.get("deepseek_api_key", "").strip())
        db.set_setting("sender_company_name", request.form.get("sender_company_name", "").strip())
        db.set_setting("product_description", request.form.get("product_description", "").strip())
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
        daily_min=config.get_daily_min_per_mailbox(),
        daily_max=config.get_daily_max_per_mailbox(),
        window_start=config.get_send_window_start_hour(),
        window_end=config.get_send_window_end_hour(),
    )


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
