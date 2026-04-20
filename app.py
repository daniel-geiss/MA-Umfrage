from flask import Flask, render_template, request, redirect, url_for, make_response
import uuid
import json
import logging
import os
import random

from datetime import datetime
from sheets_sync import append_response, load_from_sheet
from dataloader import get_part_2, get_part_1



app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

logger.setLevel(logging.DEBUG)

RESPONSES_FILE = "responses.json"
VALID_ROLES = {"Lehrer", "Mitarbeiter", "Sonstige"}

# ---------------------------------------------------------------------------
# Survey data
# ---------------------------------------------------------------------------

# Part 1 – participants rate pre-supplied grading / comment / reasoning
PART1_ITEMS = get_part_1()
'''[
    {
        "id": "EX001",
        "level": "B2",
        "example_text": "Despite the heavy rain, the outdoor concert proceeded as planned, much to the delight of the audience who had gathered in their hundreds.",
        "grading": 5,
        "comment": "The sentence demonstrates strong use of subordinating conjunctions and complex clause structure.",
        "reasoning": "The use of 'despite' correctly introduces a concessive clause. The subject-verb agreement is maintained throughout, and the participial phrase 'much to the delight of' is used appropriately to add nuance."
    },
    {
        "id": "EX002",
        "level": "C1",
        "example_text": "The scientist, whose groundbreaking research had been largely overlooked during her lifetime, were finally recognised posthumously.",
        "grading": 2,
        "comment": "There is a subject-verb agreement error in this sentence.",
        "reasoning": "The subject 'The scientist' is singular, but the verb 'were' is plural. The relative clause 'whose groundbreaking research had been largely overlooked during her lifetime' is a non-restrictive clause and does not affect the agreement between the main subject and verb. The correct form should be 'was finally recognised'."
    },
    {
        "id": "EX003",
        "level": "A2",
        "example_text": "She go to the market every Saturday with her mother and buys fresh vegetables.",
        "grading": 2,
        "comment": "There is an inconsistency in verb conjugation within the same sentence.",
        "reasoning": "The sentence has two verbs — 'go' and 'buys'. The second verb 'buys' is correctly conjugated in the third person singular present simple, but 'go' should be 'goes' to match. This is likely a simple oversight but constitutes a grammatical error at this level."
    },
]'''

# Part 2 – participants supply their own grading / comment / reasoning
PART2_ITEMS = get_part_2()
#[
#    {
#        "id": "ANN001",
#        "level": "B1",
#        "example_text": "If I would have known about the meeting, I would have prepared a presentation for it.",
#    },
#    {
#        "id": "ANN002",
#        "level": "C2",
#        "example_text": "The proliferation of disinformation across social media platforms have necessitated a fundamental rethinking of how we regulate online speech.",
#    },
#    {
#        "id": "ANN003",
#        "level": "A1",
#        "example_text": "She have three cats and two dogs at home.",
#    },
#]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_responses():
    if os.path.exists(RESPONSES_FILE):
        with open(RESPONSES_FILE, "r") as f:
            return json.load(f)
    # Local file is gone (e.g. after a container restart) — rebuild from sheet
    logger.info("responses.json not found, attempting to load from Google Sheet…")
    data = load_from_sheet()
    if data:
        save_responses(data)   # restore the local cache
        logger.info("Restored %d participant(s) from Google Sheet.", len(data))
    return data

def save_responses(data):
    with open(RESPONSES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_or_create_user_id(req, resp=None):
    user_id = req.cookies.get("survey_user_id")
    if not user_id:
        user_id = str(uuid.uuid4())
        if resp:
            resp.set_cookie("survey_user_id", user_id, max_age=60 * 60 * 24 * 30)
    return user_id

def part_progress(user_responses, part_key, items):
    done = sum(1 for i in range(len(items)) if f"{part_key}_{i}" in user_responses)
    return done, len(items)


# ---------------------------------------------------------------------------
# Routes – dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    user_id = request.cookies.get("survey_user_id")
    responses = load_responses()
    user_responses = responses.get(user_id, {}) if user_id else {}

    p1_done, p1_total = part_progress(user_responses, "p1", PART1_ITEMS)
    p2_done, p2_total = part_progress(user_responses, "p2", PART2_ITEMS)

    current_role = user_responses.get("role", "")
    resp = make_response(render_template(
        "index.html",
        p1_done=p1_done, p1_total=p1_total,
        p2_done=p2_done, p2_total=p2_total,
        current_role=current_role,
    ))
    get_or_create_user_id(request, resp)
    return resp

@app.route("/set_role", methods=["POST"])
def set_role():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))
    role = request.form.get("role", "").strip()
    if role not in VALID_ROLES:
        return redirect(url_for("index"))
    responses = load_responses()
    responses.setdefault(user_id, {})["role"] = role
    save_responses(responses)
    return redirect(url_for("index"))

# ---------------------------------------------------------------------------
# Routes – Part 1
# ---------------------------------------------------------------------------

@app.route("/part1")
def part1():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))

    responses = load_responses()
    user_responses = responses.get(user_id, {})
    assert(type(user_responses) == dict)
    if not user_responses.get("role"):
        return redirect(url_for("index"))
    
    num_done,total = part_progress(user_responses,'p1',PART1_ITEMS)
    
    for i, item in random.sample(list(enumerate(PART1_ITEMS)), total):
        if f"p1_{i}" not in user_responses:
            resp = make_response(render_template(
                "survey.html",
                item=item,
                item_index=i,
                num_done=num_done,
                total=total,
            ))
            resp.set_cookie("survey_user_id", user_id, max_age=60 * 60 * 24 * 30)
            return resp

    return redirect(url_for("part_done", part="1"))


@app.route("/part1/submit", methods=["POST"])
def part1_submit():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))

    item_index = request.form.get("item_index")
    responses = load_responses()
    role = responses.get(user_id, {}).get("role", "")
    data = {
        "rating_grading":   int(request.form.get("rating_grading")),
        "rating_comment":   request.form.get("rating_comment"),
        "rating_reasoning": request.form.get("rating_reasoning"),
        "general_comment":  request.form.get("general_comment", "").strip(),
        "role":             role,
        "submitted_at":     datetime.utcnow().isoformat(),
    }

    responses.setdefault(user_id, {})[f"p1_{item_index}"] = data
    save_responses(responses)
    append_response("p1", user_id, item_index, data)
    return redirect(url_for("part1"))


@app.route("/part1/skip", methods=["POST"])
def part1_skip():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))
    item_index = request.form.get("item_index")
    responses = load_responses()

    skip_reason = request.form.get('skip_reason') 

    role = responses.get(user_id, {}).get("role", "")
    data = {
        "skipped":      True,
        "role":         role,
        "submitted_at": datetime.utcnow().isoformat(),
        "skip_reason":skip_reason
    }
    responses.setdefault(user_id, {})[f"p1_{item_index}"] = data
    save_responses(responses)
    append_response("p1", user_id, item_index, data)
    return redirect(url_for("part1"))

# ---------------------------------------------------------------------------
# Routes – Part 2
# ---------------------------------------------------------------------------

@app.route("/part2")
def part2():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))

    responses = load_responses()
    user_responses = responses.get(user_id, {})
    if not user_responses.get("role"):
        return redirect(url_for("index"))

    #num_done = sum( 1 if key.starts_with("p2_") else 0 for key in user_responses)

    num_done,total = part_progress(user_responses,"p2",PART2_ITEMS)

    for i, item in random.sample(list(enumerate(PART2_ITEMS)),total):
        if f"p2_{i}" not in user_responses:
            resp = make_response(render_template(
                "annotate.html",
                item=item,
                item_index=i,
                num_done=num_done,
                total=total,
            ))
            resp.set_cookie("survey_user_id", user_id, max_age=60 * 60 * 24 * 30)
            return resp

    return redirect(url_for("part_done", part="2"))


@app.route("/part2/submit", methods=["POST"])
def part2_submit():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))

    item_index = request.form.get("item_index")
    responses = load_responses()
    role = responses.get(user_id, {}).get("role", "")
    data = {
        "grading":         request.form.get("grading", "").strip(),
        "comment":         request.form.get("comment", "").strip(),
        "reasoning":       request.form.get("reasoning", "").strip(),
        "general_comment": request.form.get("general_comment", "").strip(),
        "role":            role,
        "submitted_at":    datetime.utcnow().isoformat(),
    }

    responses.setdefault(user_id, {})[f"p2_{item_index}"] = data
    save_responses(responses)
    append_response("p2", user_id, item_index, data)
    return redirect(url_for("part2"))


@app.route("/part2/skip", methods=["POST"])
def part2_skip():
    user_id = request.cookies.get("survey_user_id")
    if not user_id:
        return redirect(url_for("index"))
    item_index = request.form.get("item_index")
    responses = load_responses()
    role = responses.get(user_id, {}).get("role", "")
    skip_reason = request.form.get('skip_reason')
    data = {
        "skipped":      True,
        "role":         role,
        "submitted_at": datetime.utcnow().isoformat(),
        "skip_reason": skip_reason
    }
    responses.setdefault(user_id, {})[f"p2_{item_index}"] = data
    save_responses(responses)
    append_response("p2", user_id, item_index, data)
    return redirect(url_for("part2"))

# ---------------------------------------------------------------------------
# Routes – completion
# ---------------------------------------------------------------------------

@app.route("/done/<part>")
def part_done(part):
    user_id = request.cookies.get("survey_user_id")
    responses = load_responses()
    user_responses = responses.get(user_id, {}) if user_id else {}

    p1_done, p1_total = part_progress(user_responses, "p1", PART1_ITEMS)
    p2_done, p2_total = part_progress(user_responses, "p2", PART2_ITEMS)
    all_done = (p1_done == p1_total) and (p2_done == p2_total)

    return render_template(
        "done.html",
        finished_part=part,
        p1_done=p1_done, p1_total=p1_total,
        p2_done=p2_done, p2_total=p2_total,
        all_done=all_done,
    )


if __name__ == "__main__":
    app.run(debug=True)
