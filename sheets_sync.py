"""
sheets_sync.py
--------------
Appends new survey responses to a Google Sheet after every submission.

Each part gets its own worksheet tab:
  - "Teil 1"  — ratings of pre-supplied gradings
  - "Teil 2"  — free annotations

Rows are appended, never overwritten, so every submission is preserved
even if responses.json is lost.

Setup
-----
1. Create a Google Sheet and note its Spreadsheet ID from the URL:
       https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit

2. In Google Cloud Console:
   a. Enable the Google Sheets API and Google Drive API for your project.
   b. Create a service account and download its JSON key file.

3. Share your spreadsheet with the service account's email address
   (looks like: my-bot@my-project.iam.gserviceaccount.com) — give it
   "Editor" access, just like sharing with a colleague.

4. Set these environment variables (in Railway's Variables panel):

     GOOGLE_CREDENTIALS_JSON   — the entire contents of the key JSON file,
                                  pasted as a single-line string
     GOOGLE_SPREADSHEET_ID     — the Spreadsheet ID from step 1

That's it. Every call to append_response() will add a row to the sheet.
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

logger.setLevel(logging.DEBUG)


SPREADSHEET_ID = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# Column headers for each sheet tab
PART1_HEADERS = [
    "timestamp", "user_id", "item_index",
    "rating_grading", "rating_comment", "rating_reasoning", "general_comment",
]
PART2_HEADERS = [
    "timestamp", "user_id", "item_index",
    "grading", "comment", "reasoning", "general_comment",
]


def _is_configured() -> bool:
    return bool(SPREADSHEET_ID and CREDENTIALS_JSON)


def _get_service():
    """Build and return an authenticated Google Sheets service object."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds_dict = json.loads(CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _ensure_headers(service, sheet_title: str, headers: list) -> None:
    """
    Check whether the first row of a tab already has headers.
    If the tab doesn't exist yet, create it and write the header row.
    If it exists but is empty, write the header row.
    """
    sheets = service.spreadsheets()

    # Get current sheet metadata
    meta = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
    existing_titles = [s["properties"]["title"] for s in meta["sheets"]]

    if sheet_title not in existing_titles:
        # Create the tab
        body = {"requests": [{"addSheet": {"properties": {"title": sheet_title}}}]}
        sheets.batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        # Write headers
        sheets.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_title}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        logger.info("sheets_sync: created tab '%s' with headers.", sheet_title)
        return

    # Tab exists — check if row 1 is populated
    result = sheets.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_title}!A1:Z1",
    ).execute()
    if not result.get("values"):
        sheets.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_title}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        logger.info("sheets_sync: wrote headers to empty tab '%s'.", sheet_title)


def _append_row(service, sheet_title: str, row: list) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_title}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _do_append(part: str, user_id: str, item_index: str, entry: dict) -> None:
    """Blocking append — always called from a background thread."""
    try:
        service = _get_service()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        if part == "p1":
            _ensure_headers(service, "Teil 1", PART1_HEADERS)
            row = [
                now,
                user_id,
                item_index,
                entry.get("rating_grading", ""),
                entry.get("rating_comment", ""),
                entry.get("rating_reasoning", ""),
                entry.get("general_comment", ""),
            ]
            _append_row(service, "Teil 1", row)
            logger.info("sheets_sync: appended Part 1 row for user %s item %s.", user_id, item_index)

        elif part == "p2":
            _ensure_headers(service, "Teil 2", PART2_HEADERS)
            row = [
                now,
                user_id,
                item_index,
                entry.get("grading", ""),
                entry.get("comment", ""),
                entry.get("reasoning", ""),
                entry.get("general_comment", ""),
            ]
            _append_row(service, "Teil 2", row)
            logger.info("sheets_sync: appended Part 2 row for user %s item %s.", user_id, item_index)

    except Exception:
        logger.exception("sheets_sync: failed to append row.")


def append_response(part: str, user_id: str, item_index: str, entry: dict) -> None:
    """
    Non-blocking: fire-and-forget in a background thread.
    part      — "p1" or "p2"
    user_id   — the participant's cookie UUID
    item_index — string index of the survey item
    entry     — the response dict just saved to responses.json
    """
    if not _is_configured():
        logger.debug("sheets_sync: not configured, skipping.")
        return
    t = threading.Thread(
        target=_do_append,
        args=(part, user_id, item_index, entry),
        daemon=True,
    )
    t.start()


def load_from_sheet() -> dict:
    """
    Reconstruct the full responses dict from the Google Sheet.

    Called once at startup when responses.json is missing (e.g. after a
    container restart). Returns a dict in the same shape as responses.json:

        {
          "<user_id>": {
            "p1_0": { "rating_grading": "4", ... },
            "p2_1": { "grading": "3", ... },
            ...
          },
          ...
        }

    Returns an empty dict if the sheet is not configured, unreachable,
    or has no data rows yet.
    """
    if not _is_configured():
        logger.debug("sheets_sync: not configured, skipping load.")
        return {}

    try:
        service = _get_service()
        sheets  = service.spreadsheets()
        responses: dict = {}

        # ── Part 1 ──────────────────────────────────────────────────────────
        try:
            result = sheets.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Teil 1!A1:Z",
            ).execute()
            rows = result.get("values", [])
            if len(rows) > 1:          # row 0 is the header
                headers = rows[0]
                for row in rows[1:]:
                    # Pad short rows to match header length
                    row = row + [""] * (len(headers) - len(row))
                    r = dict(zip(headers, row))
                    uid  = r.get("user_id", "")
                    idx  = r.get("item_index", "")
                    if not uid or idx == "":
                        continue
                    responses.setdefault(uid, {})[f"p1_{idx}"] = {
                        "rating_grading":   r.get("rating_grading", ""),
                        "rating_comment":   r.get("rating_comment", ""),
                        "rating_reasoning": r.get("rating_reasoning", ""),
                        "general_comment":  r.get("general_comment", ""),
                        "submitted_at":     r.get("timestamp", ""),
                    }
            logger.info("sheets_sync: loaded %d Part 1 rows from sheet.",
                        max(0, len(rows) - 1))
        except Exception:
            logger.warning("sheets_sync: could not read 'Teil 1' tab — it may not exist yet.")

        # ── Part 2 ──────────────────────────────────────────────────────────
        try:
            result = sheets.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Teil 2!A1:Z",
            ).execute()
            rows = result.get("values", [])
            if len(rows) > 1:
                headers = rows[0]
                for row in rows[1:]:
                    row = row + [""] * (len(headers) - len(row))
                    r = dict(zip(headers, row))
                    uid  = r.get("user_id", "")
                    idx  = r.get("item_index", "")
                    if not uid or idx == "":
                        continue
                    responses.setdefault(uid, {})[f"p2_{idx}"] = {
                        "grading":         r.get("grading", ""),
                        "comment":         r.get("comment", ""),
                        "reasoning":       r.get("reasoning", ""),
                        "general_comment": r.get("general_comment", ""),
                        "submitted_at":    r.get("timestamp", ""),
                    }
            logger.info("sheets_sync: loaded %d Part 2 rows from sheet.",
                        max(0, len(rows) - 1))
        except Exception:
            logger.warning("sheets_sync: could not read 'Teil 2' tab — it may not exist yet.")

        return responses

    except Exception:
        logger.exception("sheets_sync: load_from_sheet failed.")
        return {}
