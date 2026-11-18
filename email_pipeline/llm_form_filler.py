"""LLM-based contact-form field understanding.

The keyword matcher in form_crawler.py (FIELD_KEYWORDS) only handles plain
text/email/tel/textarea fields whose name/id/placeholder/label obviously say
what they are, and never touches a <select> or a radio group at all (always
left unfilled, reported as "unmatched"). Real forms are messier than that -
e.g. a WordPress Contact Form 7 form with SEPARATE Vorname/Name fields
(keyword matching puts the same value in both, since "name" is a substring
of "vorname"), or a required "Formularart" dropdown with no field left blank
or the form won't submit at all.

This asks the AI to look at EVERY field (enumerate_fields below) and decide
one of: fill it with specific text, pick a dropdown/radio option, or skip it
- with an explicit instruction to only ever touch what's REQUIRED or clearly
about routing the message (never an optional newsletter opt-in, never a
medical/insurance detail that isn't ours to answer).

Used as the FIRST choice when a Translation... no, a DeepSeek API key is
configured (classify_fields_with_llm) - form_crawler.py falls back to the
plain keyword matcher if there's no key, or if the call fails for any
reason, so the crawler never just stops working without one.
"""

import json

from . import config


def enumerate_fields(form) -> list[dict]:
    """Every input/textarea/select inside `form`, each as a dict with a
    "locator" Playwright object (used later to actually fill it) plus
    everything the LLM needs to decide what it is: tag/type, name, a best-
    effort label, whether it's required, and the available options for a
    select or radio group. Radio buttons sharing one `name` are collapsed
    into a SINGLE field entry with multiple options - picking an option
    means clicking the right radio, not filling text.
    """
    fields: list[dict] = []
    seen_radio_groups: set[str] = set()
    inputs = form.locator("input, textarea, select")
    for i in range(inputs.count()):
        el = inputs.nth(i)
        try:
            tag = (el.evaluate("e => e.tagName") or "").lower()
            field_type = (el.get_attribute("type") or "text").lower() if tag == "input" else tag
            if field_type in ("submit", "button", "hidden", "image", "reset"):
                continue

            name = el.get_attribute("name") or ""
            field_id = el.get_attribute("id") or ""
            placeholder = el.get_attribute("placeholder") or ""
            required = el.get_attribute("required") is not None or (el.get_attribute("aria-required") or "").lower() == "true"
            label_text = _label_for(el, field_id)

            if field_type == "radio":
                if not name or name in seen_radio_groups:
                    continue
                seen_radio_groups.add(name)
                fields.append(
                    {
                        "index": len(fields),
                        "tag": "radio-group",
                        "name": name,
                        "label": label_text,
                        "required": required,
                        "options": _radio_options(form, name),
                        "locator": form.locator(f'input[type="radio"][name="{name}"]'),
                    }
                )
                continue

            if field_type == "checkbox":
                fields.append(
                    {
                        "index": len(fields),
                        "tag": "checkbox",
                        "name": name,
                        "label": label_text,
                        "required": required,
                        "options": None,
                        "locator": el,
                    }
                )
                continue

            if tag == "select":
                options = el.locator("option")
                option_list = [{"value": options.nth(j).get_attribute("value") or "", "label": (options.nth(j).inner_text() or "").strip()} for j in range(options.count())]
                fields.append(
                    {
                        "index": len(fields),
                        "tag": "select",
                        "name": name,
                        "label": label_text,
                        "required": required,
                        "options": option_list,
                        "locator": el,
                    }
                )
                continue

            fields.append(
                {
                    "index": len(fields),
                    "tag": field_type if tag == "input" else tag,
                    "name": name,
                    "id": field_id,
                    "placeholder": placeholder,
                    "label": label_text,
                    "required": required,
                    "options": None,
                    "locator": el,
                }
            )
        except Exception:
            continue
    return fields


def _label_for(el, field_id: str) -> str:
    if field_id:
        try:
            label = el.page.locator(f'label[for="{field_id}"]')
            if label.count() > 0:
                return label.first.inner_text().strip()[:150]
        except Exception:
            pass
    # No <label for>: many older/table-layout forms just put the label text
    # as plain content in the nearest enclosing <p>/<label>/<div> - a best-
    # effort fallback so the LLM still has something to go on.
    try:
        return el.locator("xpath=ancestor::*[self::p or self::label or self::div][1]").first.inner_text().strip()[:150]
    except Exception:
        return ""


def _radio_options(form, name: str) -> list[dict]:
    group = form.locator(f'input[type="radio"][name="{name}"]')
    options = []
    for j in range(group.count()):
        opt = group.nth(j)
        value = opt.get_attribute("value") or ""
        opt_id = opt.get_attribute("id") or ""
        label = ""
        if opt_id:
            try:
                lbl = opt.page.locator(f'label[for="{opt_id}"]')
                if lbl.count() > 0:
                    label = lbl.first.inner_text().strip()
            except Exception:
                pass
        if not label:
            try:
                label = opt.locator("xpath=ancestor::label[1]").first.inner_text().strip()
            except Exception:
                label = value
        options.append({"value": value, "label": label})
    return options


def _strip_for_llm(fields: list[dict]) -> list[dict]:
    return [{k: v for k, v in f.items() if k != "locator"} for f in fields]


SYSTEM_PROMPT = (
    "You are filling out a contact/inquiry form on a German business website on behalf of the sender "
    "described below. You are given every field on the form as a JSON list, each with an \"index\". For EACH "
    "field, decide exactly one action:\n"
    "- fill: a text/email/tel/textarea field - \"value\" is the text to type in.\n"
    "- select: a dropdown - \"value\" is the exact option VALUE (not necessarily its label) that best fits a "
    "general business inquiry from the sender, never a specific product/medical/prescription request unless the "
    "sender info explicitly says so.\n"
    "- check: a checkbox or radio group that is REQUIRED to submit the form (e.g. a mandatory privacy-policy "
    "consent checkbox, or a required category/routing choice) - \"value\" is the exact option value to pick.\n"
    "- skip: anything NOT required and not clearly about routing this specific message (optional newsletter "
    "opt-ins, medical/insurance details, anything you are not confident about). When in doubt, skip.\n\n"
    "Rules:\n"
    "- If the form has separate first-name and last-name fields, split the sender's full name correctly - never "
    "put the same value in both.\n"
    "- Never invent information not given to you below.\n"
    "- Only select/check something that is REQUIRED (see each field's \"required\" flag) or is clearly how this "
    "message gets routed (e.g. a \"type of inquiry\" dropdown) - leave every optional field alone, including "
    "optional consent/marketing checkboxes.\n\n"
    'Respond with ONLY a JSON object: {"fields": [{"index": 0, "action": "...", "value": "..."}, ...]} - one '
    "entry per field index given to you (value omitted or empty for skip), no commentary, no markdown fences."
)


def classify_fields_with_llm(fields: list[dict], sender: dict) -> dict[int, dict] | None:
    """Returns {index: {"action": "fill"|"select"|"check"|"skip", "value": str}}
    for every field index given, or None if no AI key is configured or the
    call failed for any reason - callers fall back to the plain keyword
    matcher in that case, so a form always gets at least the old behavior."""
    api_key = config.get_deepseek_api_key()
    if not api_key or not fields:
        return None

    import openai

    client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    user_text = json.dumps({"sender": sender, "fields": _strip_for_llm(fields)}, ensure_ascii=False, indent=2)

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )
        parsed = json.loads(response.choices[0].message.content)
        result: dict[int, dict] = {}
        for entry in parsed.get("fields", []):
            index = entry.get("index")
            if isinstance(index, int):
                result[index] = {"action": entry.get("action") or "skip", "value": entry.get("value") or ""}
        return result
    except Exception:
        return None


def apply_actions(fields: list[dict], actions: dict[int, dict]) -> tuple[bool, list[dict]]:
    """Carries out the LLM's (or the keyword fallback's) per-field decision.
    Returns (filled_any, unmatched) - unmatched has the same shape
    form_crawler.py's keyword path already produces, so the UI's "N fields
    not filled" notice works the same either way."""
    filled_any = False
    unmatched = []
    for field in fields:
        action_entry = actions.get(field["index"], {"action": "skip"})
        action = action_entry.get("action", "skip")
        value = action_entry.get("value", "")
        locator = field["locator"]

        try:
            if action == "fill" and value and field["tag"] not in ("select", "checkbox", "radio-group"):
                locator.fill(value, force=True)
                filled_any = True
                continue
            if action == "select" and value and field["tag"] == "select":
                locator.select_option(value=value, force=True)
                filled_any = True
                continue
            if action == "check" and field["tag"] == "checkbox":
                locator.check(force=True)
                filled_any = True
                continue
            if action == "check" and value and field["tag"] == "radio-group":
                locator.locator(f'[value="{value}"]').first.check(force=True)
                filled_any = True
                continue
        except Exception:
            pass  # falls through to "unmatched" below - same as a classification miss

        unmatched.append(
            {
                "name": field.get("name", ""),
                "id": field.get("id", ""),
                "placeholder": field.get("placeholder", ""),
                "label": field.get("label", ""),
                "type": field["tag"],
            }
        )
    return filled_any, unmatched
