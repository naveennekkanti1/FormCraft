from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from flask_mail import Mail, Message
from functools import wraps
import os, uuid, json, io, pandas as pd
from datetime import datetime

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "default_secret")

# MongoDB setup
app.config["MONGO_URI"] = os.environ.get("MONGO_URI")
client = MongoClient(app.config["MONGO_URI"])
db = client["RAPACT"]
users_collection = db["users"]

# Mail setup
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "True") == "True"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")
mail = Mail(app)

mongo = PyMongo(app)

# Example route (home page)
@app.route("/")
def home():
    return render_template("index.html")

# Keep all your other routes here...
# IMPORTANT: Do NOT add app.run()
