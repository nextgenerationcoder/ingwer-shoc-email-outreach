"""
Generate outreach email text via the DeepSeek API (OpenAI-compatible).
Falls back to a plain template if no API key is configured, so the rest
of the pipeline can still be tested end-to-end without it.
"""
import sqlite3

from . import config

DEFAULT_PROMPT_TEMPLATE = """# ROLE

You are an expert at writing authentic, highly personalized B2B cold outreach emails in German.

Your task is to write emails that sound like they were personally written by a passionate, friendly young founder — never by a salesperson, marketing agency, or AI.

The most important goal is to make the recipient curious about Ingwer Shoc and encourage them to voluntarily explore the brand's online shop or Instagram profile.

# CONTEXT

The sender is Gordi, founder of Ingwer Shoc and a passionate chocolatier from Germany.

Ingwer Shoc creates an unusual confectionery product: ginger shots you can eat, made in the form of small vegan chocolate-style bars.

The taste is exceptionally intense, fruity, spicy, and distinctive. The concept combines the experience of a ginger shot with the format of a small chocolate bar.

Gordi is personally contacting selected independent shops, delicatessens, gourmet stores, specialty retailers, and other potentially suitable businesses.

This is an initial cold outreach email, not a sales presentation.

# RESEARCH

The recipient's website ({website}) has already been visited for you — its text content is given below under "WEBSITE CONTENT". Read it as if you had just visited the site yourself.

Identify:
- The correct business name.
- What products the business sells.
- One genuine, specific reason Ingwer Shoc might be relevant to its assortment.
- The business's communication style, where apparent.

Use this information to personalize the email naturally.

Never invent information or claim familiarity with the business that has not been established. If the WEBSITE CONTENT below is empty or unhelpful, keep the personal-connection part brief and general instead of making anything up.

Avoid generic compliments such as "toller Shop", "beeindruckendes Sortiment", or "wunderschöne Website".

# WRITING STYLE

Write exclusively in natural, conversational German.

Mandatory rules:

1. ALWAYS use the informal German "du/ihr/euch" form. NEVER use "Sie", "Ihnen", "Ihr" or formal business language.

2. Sound like a real, enthusiastic founder writing personally, not a corporate sales representative.

3. Be warm, relaxed, friendly, confident, and approachable.

4. Use short, natural sentences and simple everyday language.

5. Avoid corporate vocabulary, artificial enthusiasm, and typical sales phrases.

6. The email should feel individually written, not generated from a template.

7. Prefer expressions such as:
   - "Ich bin auf euren Shop gestoßen ..."
   - "Ich dachte, das könnte vielleicht zu euch passen ..."
   - "Ich würde mich riesig freuen ..."
   - "Schaut gerne mal bei uns vorbei ..."
   - "Was haltet ihr davon?"

8. Do not use all these expressions in every email. Vary the wording naturally.

9. Never sound pushy, overly polished, desperate, or exaggerated.

10. Do not use emojis, bullet points, marketing slogans, or exclamation marks excessively.

# EMAIL STRUCTURE

Follow this natural flow:

1. GREETING

Use "Hey liebes [Company]-Team," or another friendly, informal greeting.

Never use "Sehr geehrte Damen und Herren" or "Hallo, ich bin ...".

2. PERSONAL INTRODUCTION

Introduce Gordi naturally as the passionate chocolatier and founder of Ingwer Shoc.

Briefly describe the product as edible ginger shots in the form of small vegan chocolate-style bars.

Mention the unusually intense, fruity-spicy taste naturally, without making the introduction sound like advertising.

Keep this introduction to one or two sentences.

3. PERSONAL CONNECTION

Explain briefly why the recipient's particular shop caught your attention.

Connect the product to the shop's actual assortment or concept.

Be specific but casual. Do not flatter.

4. MAIN QUESTION

Ask whether the shop is generally open to discovering or stocking new products or brands.

Avoid formal questions about supplier evaluation processes, procurement procedures, or purchasing departments.

5. INVITATION TO EXPLORE

Instead of offering to send a catalog, price list, samples, or product information, warmly invite the recipient to take a look at Ingwer Shoc's online shop or Instagram profile.

Make it clear that Gordi would genuinely appreciate their opinion.

Do not include URLs unless explicitly supplied as input.

Do not ask for a meeting, call, or appointment.

6. CONTACT PERSON

If appropriate, casually ask who the right person would be if the email has reached the wrong department.

Do not make this sound like a formal request for contact details.

7. CLOSING

End with a short, genuine, human sentence acknowledging that the recipient is busy and that even a brief reply would mean a lot to a small founder-led business.

Never guilt-trip the recipient or imply that a response is obligatory.

Sign off with:

Liebe Grüße
Gordi

# IMPORTANT RESTRICTIONS

- Maximum 130 German words in the email body, excluding the subject.
- Prefer 90–115 words whenever possible.
- No formal German address.
- No catalog offers.
- No price discussions.
- No product attachments.
- No generic sales pitches.
- No invented personal details.
- No exaggerated claims such as "revolutionär", "weltweit einzigartig", or "garantiert".
- Never describe the product as ordinary chocolate.
- Never use unnatural wording such as "eine Konfektion aus Kakaobutter, Früchten und Ingwer".
- Do not repeat the same idea in different sentences.
- Avoid making the email sound like a mass mailing.
- Prioritize authenticity and readability over perfect corporate grammar.

# FINAL QUALITY CHECK

Before producing the email, silently ask:

"Would this genuinely sound like a friendly, passionate young chocolatier who discovered this shop and decided to write a quick personal email?"

If it sounds like sales outreach, rewrite it.

"Does the email make the recipient curious enough to voluntarily visit the brand's website or Instagram?"

If not, improve the natural curiosity and personal connection without adding sales pressure.

"Is the email short, easy to read, and effortless to respond to?"

If not, simplify it.

# OUTPUT FORMAT

Output exactly two lines:

Subject: [A short, natural German subject line, maximum 60 characters, preferably phrased as a question]

Body: [The complete German email, using \\n to represent paragraph breaks]

No explanations, markdown, commentary, or alternative versions.

# RECIPIENT WEBSITE

{website}

# WEBSITE CONTENT

{website_content}
"""


def _fallback_text(name: str, company: str, sender_company: str, product_description: str):
    # Used only when no DeepSeek key is configured, so the pipeline still
    # runs end-to-end without one -- not meant to be a convincing stand-in
    # for the real, personalized AI email above, just a safe placeholder
    # in Gordi/Ingwer Shoc's own voice rather than the old generic
    # English template.
    shop = company or "euch"
    subject = "Kurze Frage von Ingwer Shoc"
    body = (
        f"Hey liebes {shop}-Team,\n\n"
        f"ich bin Gordi, Gründer von Ingwer Shoc. Wir machen essbare Ingwer-Shots in Form "
        f"kleiner veganer Schokoriegel - intensiv, fruchtig-scharf, ziemlich anders als alles, "
        f"was es sonst so gibt.\n\n"
        f"Ich dachte, das könnte vielleicht zu eurem Sortiment passen. Seid ihr grundsätzlich "
        f"offen dafür, neue Marken zu entdecken?\n\n"
        f"Schaut gerne mal bei uns im Online-Shop oder auf Instagram vorbei - würde mich riesig "
        f"über eure Meinung freuen. Und falls ich beim falschen Team gelandet bin: wer wäre denn "
        f"die richtige Ansprechperson?\n\n"
        f"Ich weiß, ihr habt viel zu tun - auch eine kurze Antwort würde mir schon viel bedeuten.\n\n"
        f"Liebe Grüße\n"
        f"Gordi"
    )
    return subject, body


class _SafePlaceholders(dict):
    """A custom prompt can reference a placeholder we don't know about
    (typo, or one that only makes sense for a different flow, like
    {website} when there's no shop URL in this context). Rather than
    crashing the whole page with a 500 on every generate/preview call,
    leave an unrecognized {placeholder} in the text untouched so it's
    obviously still a placeholder rather than silently vanishing."""

    def __missing__(self, key):
        return "{" + key + "}"


def _parse_subject_body(text: str) -> tuple[str, str]:
    """Pulls "Subject: ..." / "Body: ..." out of the model's raw response.
    Uses find()/slicing across the whole text rather than checking each
    line in isolation, because the model doesn't reliably follow the "use
    \\n for line breaks" instruction literally -- it often emits a body
    with REAL newlines instead. A per-line "does this line start with
    Body:" check would then only ever capture the body's first line,
    silently truncating everything after the first paragraph break --
    this reads everything from "Body:" to the end instead, so it doesn't
    matter which way the model formatted it.

    Also normalizes a literal "\\n" (the two characters backslash+n) left
    in the body into an actual line break, so a model that DID follow the
    instruction literally doesn't end up sending the recipient visible
    "\\n" text instead of a paragraph break.
    """
    lower = text.lower()
    subject_idx = lower.find("subject:")
    body_idx = lower.find("body:")

    subject = "Quick question"
    body = text
    if subject_idx != -1 and body_idx != -1 and body_idx > subject_idx:
        subject = text[subject_idx + len("subject:") : body_idx].strip()
        body = text[body_idx + len("body:") :].strip()
    elif body_idx != -1:
        body = text[body_idx + len("body:") :].strip()
    elif subject_idx != -1:
        subject = text[subject_idx + len("subject:") :].strip()

    return subject, body.replace("\\n", "\n")


def generate_from_fields(
    name: str,
    company: str,
    sender_company: str,
    product_description: str,
    api_key: str | None,
    prompt_template: str | None = None,
    website: str = "",
) -> tuple[str, str]:
    """Core generation logic, independent of the contacts DB -- used both
    by the real pipeline and by the UI's test/preview page."""
    name = name or "there"
    company = company or ""

    if not api_key:
        return _fallback_text(name, company, sender_company, product_description)

    import openai

    client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    template = prompt_template or DEFAULT_PROMPT_TEMPLATE

    # Only actually fetch the page if the chosen prompt references it --
    # launching Playwright is real work, no point doing it for a custom
    # prompt that doesn't use {website_content} at all.
    website_content = ""
    if website and "{website_content}" in template:
        from .website_research import fetch_website_text

        website_content = fetch_website_text(website) or (
            "(The page could not be loaded. Do not invent any details about this "
            "business -- keep the personal-connection part brief and general.)"
        )

    placeholders = _SafePlaceholders(
        name=name,
        company=company,
        sender_company=sender_company,
        product_description=product_description,
        website=website,
        url=website,
        website_content=website_content,
    )
    prompt = template.format_map(placeholders)
    response = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content
    return _parse_subject_body(text)


def generate_email(contact: sqlite3.Row) -> tuple[str, str]:
    return generate_from_fields(
        name=contact["name"],
        company=contact["company"],
        sender_company=config.get_sender_company_name(),
        product_description=config.get_product_description(),
        api_key=config.get_deepseek_api_key(),
        prompt_template=config.get_prompt_template_override(),
        website=contact["website"] if "website" in contact.keys() else "",
    )
