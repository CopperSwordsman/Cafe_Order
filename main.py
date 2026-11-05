import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/forms.body',
    'https://www.googleapis.com/auth/forms.responses.readonly',
    'https://www.googleapis.com/auth/spreadsheets'
]

FORM_ID = '1pfUopa6oKCnm9mD1XayHGcvoba_AoyIMCH9zN0rnPV8'
SHEET_ID = '1lu5x9W68S_4fre0-62HsQEJib-F5dfmf0wTU5cVLrDA'
MENUITEMS = ["Miso Soup", "Edamame", "Gyoza", "Musubi", "Onigiri", "Curry", "Pokemon Matcha Cookies", "Magikarp Taiyaki", "Chocolate Dipped Strawberries", "Ice Cream Float", "Matcha", "Iced Coffee", "Themed Soda", "Special Water", "Ramune"]
PROCESSED_FILE = 'processed_responses.json'

def main():
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

    if len(rows) < 2:
        print("Error: Sheet must have both headers (Row 1) and stock values (Row 2).")
        return

    headers = [h.strip() for h in rows[0]]
    stock_values = rows[1]

    # Convert all initial stock values to integers immediately
    stock_map = {}
    for col_index, header_name in enumerate(headers):
        if col_index < len(stock_values):
            val = stock_values[col_index].strip()
            stock_map[header_name] = int(val) if val.isdigit() else 0
        else:
            stock_map[header_name] = 0

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

            # DEBUG output to verify what question is being scanned and matched
            print(f"DEBUG -> Question Title: '{q_title}' | Matched Menu Item: {matched_item}")

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
        for header_name in headers:
            current_qty = stock_map.get(header_name, 0)
            ordered_qty = order_totals.get(header_name, 0)
            new_qty = max(0, current_qty - ordered_qty)
            updated_stock_values.append(str(new_qty))
            stock_map[header_name] = new_qty

        # Write the updated row 2 starting at cell A2
        body = {'values': [updated_stock_values]}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range='Master!A2',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()

        # Save processed response IDs
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(list(processed_ids), f)
        print("Google Sheet inventory successfully updated!")
    else:
        print("No new form responses to process.")

    # 6. Update Form descriptions with current stock levels
    update_requests = {"requests": []}

    def getItemObject(title):
        for index, item in enumerate(form.get('items', [])):
            if item.get('title') == title:
                return item, index
        return None, None

    itemObjDict = {}
    for item in MENUITEMS:
        item_obj, index = getItemObject(item)
        if item_obj:
            itemObjDict[item] = {"item": item_obj, "index": index}

    for item_name, data in itemObjDict.items():
        item_data = data["item"]
        index = data["index"]
        current_stock = stock_map.get(item_name, 0)

        item_data["description"] = f"Stock: {current_stock}"

        update_requests["requests"].append({
            "updateItem": {
                "item": item_data,
                "location": {"index": index},
                "updateMask": "description"
            }
        })

    if update_requests["requests"]:
        print("Updating form item descriptions with current stock levels...")
        result = forms_service.forms().batchUpdate(formId=FORM_ID, body=update_requests).execute()
        print("Success! Form descriptions updated.")
    else:
        print("No item IDs found to update.")

if __name__ == '__main__':
    main()