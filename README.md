Project Management Bot V3

This project is a complete rewrite based on a new, thread-based architecture.

G-Sheet Setup

Create a new Google Sheet with two tabs:

Tab 1: Projects

Headers:

ProjectID

ChannelID

Status

Title

Deliverables

KPI

DueDate

AccountableID

DriveFolderURL

Tab 2: WorkOrders

Headers:

WorkOrderID

ProjectID

ThreadID

Status

Title

Deliverables

PushedToUserID

InProgressUserID

QA_SubmittedByID

CurrentStartTime

TotalTimeSeconds

Google Cloud Setup

Go to your Google Cloud Console project.

Enable the Google Sheets API.

Enable the Google Drive API.

Create a service account (credentials.json) and share your G-Sheet and your root G-Drive folders with the service account's email.