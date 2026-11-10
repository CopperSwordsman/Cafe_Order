import os
import json
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    'https://www.googleapis.com/auth/forms.body',
    'https://www.googleapis.com/auth/forms.responses.readonly',
    'https://www.googleapis.com/auth/spreadsheets'
]

FORM_ID = '1pfUopa6oKCnm9mD1XayHGcvoba_AoyIMCH9zN0rnPV8'
SHEET_ID = '1lu5x9W68S_4fre0-62HsQEJib-F5dfmf0wTU5cVLrDA'
MENUITEMS = ["Miso Soup", "Edamame", "(4) Gyoza", "Spam Musubi", "Pokeball Onigiri", "Japanese Beef Curry", "Matcha Cookie","Nutella Taiyaki", "(3) Chocolate Pokeball Strawberries", "Ramune","Melon Soda", "Grape Soda","Orange Soda", "Iced Coffee(oatmilk)", "Iced Coffee(2% milk)", "Matcha Latte","Special Maid Water", "Ice Cream Float Scoop"]
AVAILABILITY_ITEMS = ["10AM-11AM","11AM-12PM","12PM-1PM","1PM-2PM","2PM-3PM"]
PROCESSED_FILE = 'processed_responses.json'

def process_orders():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    forms_service = build('forms', 'v1', credentials=creds)
    sheets_service = build('sheets', 'v4', credentials=creds)

    # 1. Fetch Google Sheet data (Rows 1 and 2)
    sheet_range = 'Master!A1:Z2'
    sheet_result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=sheet_range
    ).execute()
    rows = sheet_result.get('values', [])

    availability_sheet_range = 'Times!A1:Z2'
    availability_sheet_result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=availability_sheet_range
    ).execute()
    availability_rows = availability_sheet_result.get('values', [])

    if len(rows) < 2:
        print("Error: Sheet must have both headers (Row 1) and stock values (Row 2).")
        return

    headers = [h.strip() for h in rows[0]]
    stock_values = rows[1]

    availability_headers = [h.strip() for h in availability_rows[0]]
    availability_values = availability_rows[1] if len(availability_rows) > 1 else []

    # Convert all initial stock values to integers immediately
    stock_map = {}
    availability_map = {}
    for col_index, header_name in enumerate(headers):
        if col_index < len(stock_values):
            val = stock_values[col_index].strip()
            stock_map[header_name] = int(val) if val.isdigit() else 0
        else:
            stock_map[header_name] = 0

    for col_index, header_name in enumerate(availability_headers):
        if col_index < len(availability_values):
            val = availability_values[col_index].strip()
            availability_map[header_name] = int(val) if val.isdigit() else 0
        else:
            availability_map[header_name] = 0

    # 2. Fetch Form definition
    form = forms_service.forms().get(formId=FORM_ID).execute()

    question_id_to_title = {}
    for item in form.get('items', []):
        if 'questionItem' in item:
            q_id = item['questionItem']['question']['questionId']
            question_id_to_title[q_id] = item.get('title') or ""

    # 3. Read processed response IDs
    processed_ids = set()
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                processed_ids = set(json.load(f))
        except json.decoder.JSONDecodeError:
            processed_ids = set()

    # 4. Fetch Form Responses
    response_result = forms_service.forms().responses().list(formId=FORM_ID).execute()
    responses = response_result.get('responses', [])

    new_processed_count = 0
    order_totals = {item: 0 for item in MENUITEMS}
    time_slots = {item: 0 for item in AVAILABILITY_ITEMS}

    for resp in responses:
        resp_id = resp.get('responseId')
        if resp_id in processed_ids:
            continue

        answers = resp.get('answers', {})
        for q_id, answer_data in answers.items():
            q_title = question_id_to_title.get(q_id, "") or ""

            # Smart title matching to connect question title with menu item
            matched_item = None
            for menu_item in MENUITEMS:
                if menu_item.lower() in q_title.lower():
                    matched_item = menu_item
                    break

            # Timeslot Parsing (removes counts like " (24)" from user responses)
            if "time" in q_title.lower() or "slot" in q_title.lower():
                choice_answers = answer_data.get('choiceAnswers', {}).get('answers', [])
                text_answers = answer_data.get('textAnswers', {}).get('answers', [])

                time_val = ""
                if choice_answers:
                    time_val = choice_answers[0].get('value', '').strip()
                elif text_answers:
                    time_val = text_answers[0].get('value', '').strip()

                base_time = time_val.split('(')[0].strip()

                matched_slot = None
                for slot in AVAILABILITY_ITEMS:
                    if slot.lower().replace(" ", "") == base_time.lower().replace(" ", ""):
                        matched_slot = slot
                        break

                if matched_slot:
                    time_slots[matched_slot] += 1

            if matched_item in order_totals:
                choice_answers = answer_data.get('choiceAnswers', {}).get('answers', [])
                text_answers = answer_data.get('textAnswers', {}).get('answers', [])

                val = "0"
                if choice_answers:
                    val = choice_answers[0].get('value', '0').strip()
                elif text_answers:
                    val = text_answers[0].get('value', '0').strip()

                if val.isdigit():
                    order_totals[matched_item] += int(val)

        processed_ids.add(resp_id)
        new_processed_count += 1

    # 5. Update Google Sheet if new responses exist
    if new_processed_count > 0:
        print(f"Found {new_processed_count} new form response(s). Updating stock...")

        updated_stock_values = []
        updated_time_values = []
        for header_name in headers:
            current_qty = stock_map.get(header_name, 0)
            ordered_qty = order_totals.get(header_name, 0)
            new_qty = max(0, current_qty - ordered_qty)
            updated_stock_values.append(str(new_qty))
            stock_map[header_name] = new_qty

        for header_name in availability_headers:
            current_qty = availability_map.get(header_name, 0)
            ordered_qty = time_slots.get(header_name, 0)
            new_qty = max(0, current_qty - ordered_qty)
            updated_time_values.append(str(new_qty))
            availability_map[header_name] = new_qty

        # Write updated rows back to Sheets
        body = {'values': [updated_stock_values]}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range='Master!A2',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        body = {'values': [updated_time_values]}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range='Times!A2',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()

        # Save processed response IDs
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(list(processed_ids), f)
        print("Google Sheet inventory successfully updated!")

        # 6. Update Form descriptions & choice options with current stock levels
        update_requests = {"requests": []}

        def getItemObject(title):
            for index, item in enumerate(form.get('items', [])):
                if item.get('title') == title:
                    return item, index
            return None, None

        # Update Menu Item stock descriptions
        for item_name in MENUITEMS:
            item_obj, index = getItemObject(item_name)
            if item_obj:
                current_stock = stock_map.get(item_name, 0)
                item_obj["description"] = f"Stock: {current_stock}"
                update_requests["requests"].append({
                    "updateItem": {
                        "item": item_obj,
                        "location": {"index": index},
                        "updateMask": "description"
                    }
                })

        # Update Timeslot Question choices with live counts: "10AM-11AM (24)"
        for index, item in enumerate(form.get('items', [])):
            title = item.get('title', '').lower()
            if "time" in title or "slot" in title:
                question_item = item.get('questionItem', {})
                question = question_item.get('question', {})
                choice_question = question.get('choiceQuestion', {})

                if choice_question:
                    new_options = []
                    for slot in AVAILABILITY_ITEMS:
                        remaining_stock = availability_map.get(slot, 0)
                        new_options.append({"value": f"{slot} ({remaining_stock} available)"})

                    choice_question['options'] = new_options

                    update_requests["requests"].append({
                        "updateItem": {
                            "item": item,
                            "location": {"index": index},
                            "updateMask": "questionItem.question.choiceQuestion.options"
                        }
                    })

        if update_requests["requests"]:
            print("Updating form item descriptions and timeslot options...")
            forms_service.forms().batchUpdate(formId=FORM_ID, body=update_requests).execute()
            print("Success! Form options and descriptions updated.")
    else:
        print("No new form responses to process.")

def safe_main():
    print("Starting continuous inventory worker. Press Ctrl+C to stop.")
    retry_delay = 30  # Initial delay if a rate limit is hit

    while True:
        try:
            process_orders()
            retry_delay = 30  # Reset backoff on success

            # Poll every 60 seconds (prevents quota exhaustion)
            time.sleep(60)

        except HttpError as e:
            if e.resp.status == 429:
                print(f"[Rate Limit Hit] HTTP 429. Backing off for {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 300)  # Exponential backoff capped at 5 minutes
            else:
                print(f"[API Error] Encountered HTTP error: {e}")
                time.sleep(60)
        except Exception as e:
            print(f"[Unexpected Error]: {e}")
            time.sleep(60)

if __name__ == '__main__':
    safe_main()