import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request, jsonify
import random
import datetime
import os.path

# --- <<< NEW: Google Drive Imports >>> ---
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.service_account import Credentials

# --- <<< NEW: Import all secrets from config.py >>> ---
try:
    from config import (
        SHEET_URL, 
        ACTIVE_PROJECTS_FOLDER_ID, 
        FINISHED_PROJECTS_FOLDER_ID
    )
except ImportError:
    print("FATAL ERROR: config.py not found or missing variables.")
    print("Please create config.py and add your secrets.")
    exit()

CRED_FILE = "credentials.json"
# --- G-SHEET & G-DRIVE SETUP ---
SCOPES = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/drive']

# --- G-Sheet Client (Old way) ---
gs_creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, SCOPES)
gs_client = gspread.authorize(gs_creds)
try:
    sheet = gs_client.open_by_url(SHEET_URL)
except gspread.exceptions.SpreadsheetNotFound:
    print(f"FATAL ERROR: Could not find G-Sheet at URL: {SHEET_URL}")
    print("Please check SHEET_URL in config.py")
    exit()

# --- G-Drive Client (New way) ---
try:
    drive_creds = Credentials.from_service_account_file(CRED_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=drive_creds)
except FileNotFoundError:
    print(f"FATAL ERROR: credentials.json not found.")
    print("Please add your credentials.json file to the project folder.")
    exit()

try:
    projects_sheet = sheet.worksheet("Projects")
    workorders_sheet = sheet.worksheet("WorkOrders")
except gspread.exceptions.WorksheetNotFound:
    print("FATAL ERROR: Please create 'Projects' and 'WorkOrders' tabs in your G-Sheet.")
    exit()

# --- FLASK API SETUP ---
app = Flask(__name__)

# --- Helper Functions ---
def find_row(worksheet, key, value):
    """Finds a row in a worksheet by matching a key (column header) and value."""
    try:
        all_data = worksheet.get_all_records(head=1)
        for i, row in enumerate(all_data):
            if str(row.get(key)) == str(value):
                return row, i + 2  # Return the row data and the G-Sheet row number
        return None, None
    except Exception as e:
        print(f"API HELPER ERROR (find_row): {e}")
        return None, None

def update_cells(worksheet, row_num, headers_to_update: dict):
    """Updates a batch of cells in a specific row."""
    try:
        headers = worksheet.row_values(1)
        cells_to_update = []
        for key, value in headers_to_update.items():
            if key in headers:
                col = headers.index(key) + 1
                cells_to_update.append(gspread.Cell(row_num, col, str(value)))
        if cells_to_update:
            worksheet.update_cells(cells_to_update)
        return True
    except Exception as e:
        print(f"API HELPER ERROR (update_cells): {e}")
        return False

# --- <<< NEW: Google Drive Helpers >>> ---
def create_gdrive_folder(name, parent_folder_id):
    """Creates a new folder in Google Drive."""
    try:
        file_metadata = {
            'name': name,
            'parents': [parent_folder_id],
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = drive_service.files().create(body=file_metadata, fields='id, webViewLink').execute()
        return folder.get('id'), folder.get('webViewLink')
    except HttpError as e:
        print(f"API GDRIVE ERROR (create_folder): {e}")
        return None, None
    except Exception as e:
        print(f"API GDRIVE ERROR (non-HTTP): {e}")
        return None, None

def move_gdrive_folder(folder_id, new_parent_id, old_parent_id):
    """Moves a G-Drive folder to a new parent (e.g., to 'Finished')."""
    try:
        file = drive_service.files().update(
            fileId=folder_id,
            addParents=new_parent_id,
            removeParents=old_parent_id,
            fields='id, parents'
        ).execute()
        return True
    except HttpError as e:
        print(f"API GDRIVE ERROR (move_folder): {e}")
        return False

# --- ================================== ---
# --- PROJECT ENDPOINTS
# --- ================================== ---

@app.route('/project', methods=['POST'])
def create_project():
    data = request.json
    try:
        # 1. Create G-Drive Folder
        folder_name = data['Title']
        folder_id, folder_url = create_gdrive_folder(folder_name, ACTIVE_PROJECTS_FOLDER_ID)
        
        if not folder_id:
            print("API WARNING: Could not create G-Drive folder. Proceeding without it.")
            folder_url = "" # Set to blank if creation failed

        # 2. Create Project in G-Sheet
        project_id = f"proj-{random.randint(1000, 9999)}"
        new_row = [
            project_id,
            str(data['ChannelID']),
            "Active", # Status
            data['Title'],
            data['Deliverables'],
            data['KPI'],
            data['DueDate'],
            str(data['AccountableID']),
            folder_url
        ]
        projects_sheet.append_row(new_row, value_input_option='USER_ENTERED')
        
        new_project_data, _ = find_row(projects_sheet, "ProjectID", project_id)
        return jsonify({"status": "success", "project": new_project_data}), 201
        
    except Exception as e:
        print(f"API ERROR (create_project): {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/project/<string:project_id>', methods=['GET'])
def get_project(project_id):
    row_data, row_num = find_row(projects_sheet, "ProjectID", project_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    return jsonify({"status": "success", "project": row_data}), 200

@app.route('/project/<string:project_id>', methods=['PUT'])
def update_project(project_id):
    data = request.json
    row_data, row_num = find_row(projects_sheet, "ProjectID", project_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    
    # We only allow editing these fields
    allowed_headers = ["Title", "Deliverables", "KPI", "DueDate", "AccountableID"]
    headers_to_update = {k: v for k, v in data.items() if k in allowed_headers}
    
    if not update_cells(projects_sheet, row_num, headers_to_update):
        return jsonify({"status": "error", "message": "Failed to update G-Sheet cells"}), 500
        
    updated_data, _ = find_row(projects_sheet, "ProjectID", project_id)
    return jsonify({"status": "success", "project": updated_data}), 200

@app.route('/project/<string:project_id>/finish', methods=['PUT'])
def finish_project(project_id):
    row_data, row_num = find_row(projects_sheet, "ProjectID", project_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Project not found"}), 404
        
    # 1. Move G-Drive Folder
    try:
        folder_url = row_data.get('DriveFolderURL')
        if folder_url:
            folder_id = folder_url.split('/')[-1].split('?')[0] # Handle different URL formats
            move_gdrive_folder(folder_id, FINISHED_PROJECTS_FOLDER_ID, ACTIVE_PROJECTS_FOLDER_ID)
            print(f"API: Moved G-Drive folder for {project_id} to Finished.")
    except Exception as e:
        print(f"API WARNING (finish_project): Could not move G-Drive folder. {e}")

    # 2. Update G-Sheet
    finish_date = datetime.date.today().isoformat()
    headers_to_update = {"Status": "Finished", "DueDate": finish_date}
    
    if not update_cells(projects_sheet, row_num, headers_to_update):
        return jsonify({"status": "error", "message": "Failed to update G-Sheet cells"}), 500
        
    updated_data, _ = find_row(projects_sheet, "ProjectID", project_id)
    return jsonify({"status": "success", "project": updated_data}), 200

@app.route('/projects/active', methods=['GET'])
def get_active_projects():
    all_data = projects_sheet.get_all_records()
    active_projects = [p for p in all_data if p.get('Status') == 'Active']
    return jsonify({"status": "success", "projects": active_projects}), 200

# --- ================================== ---
# --- WORK ORDER ENDPOINTS
# --- ================================== ---

@app.route('/workorder', methods=['POST'])
def create_work_order():
    data = request.json
    try:
        # 1. Get Parent Project G-Drive URL
        project_data, _ = find_row(projects_sheet, "ProjectID", data['ProjectID'])
        if not project_data:
            return jsonify({"status": "error", "message": "Parent project not found"}), 404
        
        # 2. Create G-Drive Subfolder
        parent_folder_url = project_data.get('DriveFolderURL')
        subfolder_url = ""
        if parent_folder_url:
            parent_folder_id = parent_folder_url.split('/')[-1].split('?')[0] # Handle different URL formats
            _, subfolder_url = create_gdrive_folder(data['Title'], parent_folder_id)
            if not subfolder_url:
                print("API WARNING: Could not create G-Drive subfolder.")
        
        # 3. Create Work Order in G-Sheet
        wo_id = f"wo-{data['ProjectID'].split('-')[-1]}-{random.randint(100, 999)}"
        new_row = [
            wo_id,
            data['ProjectID'],
            str(data['ThreadID']),
            "Open", # Status
            data['Title'],
            data['Deliverables'],
            str(data['PushedToUserID'] or ""),
            "", # InProgressUserID
            "", # QA_SubmittedByID
            "", # CurrentStartTime
            0   # TotalTimeSeconds
        ]
        workorders_sheet.append_row(new_row, value_input_option='USER_ENTERED')
        
        new_wo_data, _ = find_row(workorders_sheet, "WorkOrderID", wo_id)
        # We must add the subfolder URL manually as it's not in the sheet
        new_wo_data['SubfolderURL'] = subfolder_url 
        
        return jsonify({"status": "success", "workorder": new_wo_data}), 201
        
    except Exception as e:
        print(f"API ERROR (create_work_order): {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/workorder/<string:wo_id>', methods=['GET'])
def get_work_order(wo_id):
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
        
    # We must manually add the G-Drive subfolder URL
    try:
        project_data, _ = find_row(projects_sheet, "ProjectID", row_data['ProjectID'])
        parent_folder_url = project_data.get('DriveFolderURL')
        subfolder_url = ""
        if parent_folder_url:
            parent_folder_id = parent_folder_url.split('/')[-1].split('?')[0] # Handle different URL formats
            # This is slow, but the only way to find it
            q = f"'{parent_folder_id}' in parents and name='{row_data['Title']}' and mimeType='application/vnd.google-apps.folder'"
            response = drive_service.files().list(q=q, fields='files(id, webViewLink)').execute()
            files = response.get('files', [])
            if files:
                subfolder_url = files[0].get('webViewLink')
        row_data['SubfolderURL'] = subfolder_url
    except Exception as e:
        print(f"API WARNING (get_work_order): Could not find G-Drive subfolder. {e}")
        row_data['SubfolderURL'] = ""
        
    return jsonify({"status": "success", "workorder": row_data}), 200

@app.route('/workorder/<string:wo_id>', methods=['PUT'])
def update_work_order(wo_id):
    data = request.json
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
    
    allowed_headers = ["Title", "Deliverables", "PushedToUserID"]
    headers_to_update = {k: v for k, v in data.items() if k in allowed_headers}
    
    if not update_cells(workorders_sheet, row_num, headers_to_update):
        return jsonify({"status": "error", "message": "Failed to update G-Sheet cells"}), 500
        
    updated_data, _ = find_row(workorders_sheet, "WorkOrderID", wo_id)
    return jsonify({"status": "success", "workorder": updated_data}), 200

@app.route('/workorders/inprogress', methods=['GET'])
def get_in_progress_work_orders():
    all_data = workorders_sheet.get_all_records()
    in_progress = [w for w in all_data if w.get('Status') == 'InProgress']
    return jsonify({"status": "success", "workorders": in_progress}), 200

@app.route('/workorder/<string:wo_id>/start', methods=['PUT'])
def start_work_order(wo_id):
    data = request.json
    user_id = str(data['UserID'])
    
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
        
    pushed_to = str(row_data.get('PushedToUserID') or "")
    if pushed_to and pushed_to != user_id:
        return jsonify({"status": "error", "message": "This work order is assigned to another user."}), 403
        
    headers = {
        "Status": "InProgress",
        "InProgressUserID": user_id,
        "CurrentStartTime": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    update_cells(workorders_sheet, row_num, headers)
    return jsonify({"status": "success"}), 200

def _log_time(row_data, row_num):
    """Helper to calculate and log time, returning the total time."""
    start_time_str = row_data.get('CurrentStartTime')
    if not start_time_str:
        return int(row_data.get('TotalTimeSeconds') or 0) # Return existing time
        
    start_time = datetime.datetime.fromisoformat(start_time_str)
    time_spent = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
    
    total_spent = float(row_data.get('TotalTimeSeconds', 0))
    new_total_time = round(time_spent + total_spent)
    
    headers = {
        "TotalTimeSeconds": new_total_time,
        "CurrentStartTime": "",
        "InProgressUserID": ""
    }
    update_cells(workorders_sheet, row_num, headers)
    return new_total_time

@app.route('/workorder/<string:wo_id>/pause', methods=['PUT'])
def pause_work_order(wo_id):
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
        
    _log_time(row_data, row_num) # Log time and clear user
    update_cells(workorders_sheet, row_num, {"Status": "Open"})
    return jsonify({"status": "success"}), 200

@app.route('/workorder/<string:wo_id>/finish', methods=['PUT'])
def finish_work_order(wo_id):
    data = request.json
    user_id = str(data['UserID']) # This is the user who hit "Finish"
    
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
        
    _log_time(row_data, row_num) # Log time and clear user
    headers = {
        "Status": "InQA",
        "QA_SubmittedByID": user_id
    }
    update_cells(workorders_sheet, row_num, headers)
    return jsonify({"status": "success"}), 200

@app.route('/workorder/<string:wo_id>/approve', methods=['PUT'])
def approve_work_order(wo_id):
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
    
    headers = {
        "Status": "Approved",
        "InProgressUserID": "",
        "CurrentStartTime": "",
        "QA_SubmittedByID": ""
    }
    update_cells(workorders_sheet, row_num, headers)
    return jsonify({"status": "success"}), 200

@app.route('/workorder/<string:wo_id>/rework', methods=['PUT'])
def rework_work_order(wo_id):
    row_data, row_num = find_row(workorders_sheet, "WorkOrderID", wo_id)
    if not row_data:
        return jsonify({"status": "error", "message": "Work order not found"}), 404
        
    headers = {
        "Status": "Open",
        "InProgressUserID": "",
        "CurrentStartTime": "",
        "QA_SubmittedByID": ""
    }
    update_cells(workorders_sheet, row_num, headers)
    return jsonify({"status": "success"}), 200

# --- MAIN ---
if __name__ == '__main__':
    print("Database API is running on http://127.0.0.1:5000")
    app.run(port=5000, debug=True)