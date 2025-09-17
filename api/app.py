from flask import Flask, render_template, request, redirect, url_for, session, flash, url_for,send_file,jsonify,Response
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from bson import ObjectId,errors
from werkzeug.utils import secure_filename
from datetime import datetime,timedelta
from pymongo import MongoClient,DESCENDING
import os,uuid,smtplib,threading,gridfs,logging,io,random,docx,qrcode,base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from flask_mail import Mail, Message
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from functools import wraps
import json
import pandas as pd
import io
from dotenv import load_dotenv
import os

load_dotenv()  

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
app.config['MONGO_URI'] = os.environ.get("MONGO_URI")
client = MongoClient(app.config['MONGO_URI'])
db = client['RAPACT']
users_collection = db['users']
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASSWORD")
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get("MAIL_DEFAULT_SENDER")
otp_store = {}  
mail = Mail(app)

mongo = PyMongo(app)
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'admin':
            flash("Unauthorized access.", "danger")
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

# -------------------------------------
# Admin: Edit/Create Form Schema
# -------------------------------------
@app.route('/form_builder/<form_id>', methods=['GET', 'POST'])
@admin_required
def admin_form_builder(form_id):
    existing = mongo.db.form_schemas.find_one({"form_id": form_id})

    if request.method == 'POST':
        raw_schema = request.form.get('form_schema_json')
        status = request.form.get('status', 'closed')
        title = request.form.get('form_title', 'Untitled Form')

        try:
            schema = json.loads(raw_schema)
            mongo.db.form_schemas.update_one(
                {"form_id": form_id},
                {"$set": {
                    "form_schema": schema,
                    "status": status,
                    "title": title
                }},
                upsert=True
            )
            flash("Form schema saved successfully.", "success")
        except Exception as e:
            flash(f"Invalid JSON schema: {str(e)}", "danger")

        return redirect(url_for('admin_form_builder', form_id=form_id))

    # Use get with default value to avoid KeyError
    schema = existing.get('form_schema', []) if existing else []
    status = existing.get('status', 'open') if existing else 'open'
    title = existing.get('title', 'Untitled Form') if existing else 'Untitled Form'

    return render_template('admin_form_builder.html',
                           form_schema=schema, status=status, title=title, form_id=form_id)


@app.route('/create_form')
@admin_required
def admin_create_form():
    # Generate a unique form_id -- can also customize to slugify titles in a real app
    new_form_id = str(uuid.uuid4())[:8]  # 8-char unique ID like 'a1b2c3d4'

    # Insert new empty form with defaults
    mongo.db.form_schemas.insert_one({
        "form_id": new_form_id,
        "title": "Untitled Form",
        "status": "open",
        "form_schema": []
    })

    # Redirect admin to edit new form
    return redirect(url_for('admin_form_builder', form_id=new_form_id))

@app.route('/delete_form/<form_id>', methods=['POST'])
@admin_required
def admin_delete_form(form_id):
    # Delete form schema document
    mongo.db.form_schemas.delete_one({"form_id": form_id})
    # Optionally, delete all submissions related to this form
    mongo.db.form_submissions.delete_many({"form_id": form_id})

    flash(f"Form '{form_id}' and its submissions have been deleted.", "success")
    return redirect(url_for('admin_form_list'))



# -------------------------------------
# Admin: View Form Submissions
# -------------------------------------
@app.route('/form_submissions/<form_id>')
@admin_required
def admin_form_submissions(form_id):
    submissions = list(mongo.db.form_submissions.find({"form_id": form_id}).sort("submitted_at", -1))
    return render_template('admin_form_submissions.html', submissions=submissions, form_id=form_id)


@app.route('/download_excel/<form_id>')
@admin_required
def download_excel(form_id):
    # Query MongoDB for submissions for the given form_id, sorted by submitted_at descending
    submissions = list(mongo.db.form_submissions.find({"form_id": form_id}).sort("submitted_at", -1))

    if not submissions:
        abort(404, description="No submissions found for this form.")

    rows = []
    for sub in submissions:
        row = {
            'ID': str(sub['_id']),  # Convert ObjectId to string
            'User Email': sub.get('user_email', 'N/A'),
            'Submitted At': sub['submitted_at'].strftime('%Y-%m-%d %H:%M:%S'),
        }
        for key, value in sub['submission_data'].items():
            col_name = key.replace('_', ' ').capitalize()
            row[col_name] = value
        rows.append(row)

    df = pd.DataFrame(rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Submissions')
    output.seek(0)

    return send_file(
        output,
        download_name=f'submissions_{form_id}.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# -------------------------------------
# Admin: Send Notification Email
# -------------------------------------
@app.route('/send_email', methods=['GET', 'POST'])
@admin_required
def admin_send_email():
    if request.method == 'POST':
        subject = request.form.get('subject')
        message_body = request.form.get('message_body')
        action_url = request.form.get('action_url')
        send_to_all = request.form.get('send_to_all') == 'yes'  # radio button

        if send_to_all:
            emails_cursor = mongo.db.users.distinct('email')
            recipient_emails = [em for em in emails_cursor if em]
        else:
            manual_emails_text = request.form.get('recipient_emails', '')
            recipient_emails = [em.strip() for em in manual_emails_text.split(',') if em.strip()]

        if not recipient_emails:
            flash("No recipient emails provided.", "warning")
            return redirect(url_for('admin_send_email'))

        for recipient_email in recipient_emails:
            msg = Message(subject=subject, recipients=[recipient_email])
            msg.html = render_template(
                "general_notification.html",
                recipient_name="Patient",
                subject=subject,
                message_body=message_body,
                action_url=action_url
            )
            try:
                mail.send(msg)
            except Exception as e:
                flash(f"Failed to send to {recipient_email}: {e}", "danger")

        flash("Emails sent successfully!", "success")
        return redirect(url_for('admin_send_email'))

    return render_template('admin_send_email.html')

# -------------------------------------
# Helper: Get Form Info by ID
# -------------------------------------
def get_form_info(form_id):
    form_doc = mongo.db.form_schemas.find_one({"form_id": form_id})
    return form_doc

# -------------------------------------
# User: Submit Dynamic Form
# -------------------------------------
@app.route('/form/<form_id>', methods=['GET', 'POST'])
def user_dynamic_form(form_id):
    form_info = get_form_info(form_id)
    if not form_info:
        return "Form not found.", 404

    if form_info.get('status', 'open') != 'open':
        return render_template('form_closed.html', title=form_info.get('title', 'Form Closed'))

    form_schema = form_info.get('form_schema', [])

    if request.method == 'POST':
        data = {field['name']: request.form.get(field['name']) for field in form_schema}
        user_email = data.get('email')

        mongo.db.form_submissions.insert_one({
            "form_id": form_id,
            "user_email": user_email,
            "submission_data": data,
            "submitted_at": datetime.utcnow()
        })
        flash("Thank you! Your response has been recorded.", "success")
        return redirect(url_for('user_dynamic_form', form_id=form_id))

    return render_template('dynamic_form.html', form_schema=form_schema, form_title=form_info.get('title', 'Form'))

@app.route('/forms')
@admin_required
def admin_form_list():
    # Fetch all forms, count responses per form
    forms_cursor = mongo.db.form_schemas.find()
    forms = []
    for f in forms_cursor:
        response_count = mongo.db.form_submissions.count_documents({"form_id": f['form_id']})
        forms.append({
            "form_id": f['form_id'],
            "title": f.get('title', 'Untitled Form'),
            "status": f.get('status', 'open'),
            "response_count": response_count
        })
    return render_template('admin_form_list.html', forms=forms)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    elif request.method == 'POST':
        data = request.form
        email = data.get("email")
        password = data.get("password")
        captcha_input = data.get("captcha")
        captcha_value = data.get("captchaValue")
        remember_me = True if data.get("rememberMe") else False

        # Validate required fields
        if not (email and password):
            flash("Email and password are required", "error")
            return redirect(url_for('login'))

        # Validate CAPTCHA
        if not captcha_input or captcha_input != captcha_value:
            flash("CAPTCHA verification failed. Please try again.", "error")
            return redirect(url_for('login'))

        # Check if user exists and password is correct
        user = users_collection.find_one({"email": email})

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid credentials", "error")
            return redirect(url_for('login'))

        # Set session variables
        session["user_id"] = str(user["_id"])
        session["role"] = user.get("role", "admin")  # default role is admin

        # Set session permanence based on remember me checkbox
        session.permanent = remember_me

        flash("You have successfully logged in!", "success")
        return redirect(url_for('admin_form_list'))
    
@app.route('/')
def home():
    return render_template('index.html')
    
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('home'))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
