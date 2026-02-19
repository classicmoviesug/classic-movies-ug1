# ===== LOAD ENV =====
from dotenv import load_dotenv
load_dotenv()

import os
import json
import requests
import boto3
from boto3.s3.transfer import TransferConfig
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

# ===== FLUTTERWAVE CONFIG (UNCHANGED) =====
FLUTTERWAVE_SECRET_KEY = os.getenv("FLUTTERWAVE_SECRET_KEY")
FLUTTERWAVE_BASE_URL = "https://api.flutterwave.com/v3"

# ===== R2 CONFIG (ADDED) =====
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.getenv("R2_BUCKET_NAME")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE")

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto"
)

transfer_config = TransferConfig(
    multipart_threshold=50 * 1024 * 1024,
    multipart_chunksize=50 * 1024 * 1024
)

# ===== FLASK APP =====
app = Flask(__name__, static_folder="static")

# ===== LOCAL FOLDERS (KEPT – NOT USED FOR MOVIES ANYMORE) =====
MOVIE_FOLDER = "static/movies"
IMAGE_FOLDER = "static/images"
TEMPLATE_FOLDER = "static/templates"

os.makedirs(MOVIE_FOLDER, exist_ok=True)
os.makedirs(IMAGE_FOLDER, exist_ok=True)
os.makedirs(TEMPLATE_FOLDER, exist_ok=True)

# ===== FILE RULES =====
ALLOWED_MOVIE_EXT = {"mp4", "mov", "avi", "mkv"}
ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "gif"}

MOVIES_JSON = os.path.join(app.root_path, "movies.json")

# ===== HELPERS (UNCHANGED + R2 ADDED) =====
def allowed_file(filename, allowed_set):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_set

def load_movies():
    if os.path.exists(MOVIES_JSON):
        with open(MOVIES_JSON, "r") as f:
            return json.load(f)
    return []

def save_movies(movies):
    with open(MOVIES_JSON, "w") as f:
        json.dump(movies, f, indent=2)

def upload_to_r2(file_obj, key):
    s3.upload_fileobj(
        file_obj,
        R2_BUCKET,
        key,
        ExtraArgs={
            "ContentType": file_obj.content_type,
            "ACL": "public-read"
        },
        Config=transfer_config
    )

# ===== ROUTES (UNCHANGED) =====
@app.route("/")
def home():
    return app.send_static_file("index.html")

@app.route("/player.html")
def player_page():
    return app.send_static_file("player.html")

@app.route("/add_movie")
def add_movie_page():
    return app.send_static_file("templates/addmovies.html")

# ===== UPLOAD MOVIE (ONLY PLACE MODIFIED) =====
@app.route("/upload_movie", methods=["POST"])
def upload_movie():
    if "movie_file" not in request.files or "poster_file" not in request.files:
        return jsonify({"status": "error", "message": "Movie or poster missing"}), 400

    movie_file = request.files["movie_file"]
    poster_file = request.files["poster_file"]
    title = request.form.get("title")
    category = request.form.get("category")

    if not title or not category:
        return jsonify({"status": "error", "message": "Title and category required"}), 400

    if not allowed_file(movie_file.filename, ALLOWED_MOVIE_EXT):
        return jsonify({"status": "error", "message": "Invalid movie type"}), 400

    if not allowed_file(poster_file.filename, ALLOWED_IMAGE_EXT):
        return jsonify({"status": "error", "message": "Invalid image type"}), 400

    movie_name = secure_filename(movie_file.filename)
    poster_name = secure_filename(poster_file.filename)

    movie_key = f"movies/{movie_name}"
    poster_key = f"posters/{poster_name}"

    # 🔥 UPLOAD TO R2 (FIX FOR LARGE FILES)
    upload_to_r2(movie_file, movie_key)
    upload_to_r2(poster_file, poster_key)

    movie_url = f"{R2_PUBLIC_BASE}/{movie_key}"
    poster_url = f"{R2_PUBLIC_BASE}/{poster_key}"

    movies = load_movies()
    movies.append({
        "id": len(movies) + 1,
        "title": title,
        "category": category,
        "movie": movie_url,
        "poster": poster_url
    })

    save_movies(movies)

    return jsonify({"status": "success", "message": "Movie uploaded successfully"})

@app.route("/movies")
def get_movies():
    return jsonify(load_movies())

# ===== PAYMENT (UNCHANGED – YOUR HARD WORK IS SAFE) =====
@app.route("/pay", methods=["POST"])
def pay():
    data = request.get_json()
    phone = data.get("phone")
    amount = data.get("amount")
    movie_id = data.get("movie_id")

    tx_ref = f"movie_{movie_id}"

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "UGX",
        "payment_options": "mobilemoneyuganda",
        "redirect_url": f"{request.host_url}player.html?movie={movie_id}",
        "customer": {
            "phonenumber": phone,
            "email": "customer@example.com",
            "name": "Movie Customer"
        },
        "customizations": {
            "title": "Classic Movies UG",
            "description": "Movie purchase"
        }
    }

    headers = {
        "Authorization": f"Bearer {FLUTTERWAVE_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            f"{FLUTTERWAVE_BASE_URL}/payments",
            json=payload,
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/verify/<tx_ref>")
def verify(tx_ref):
    headers = {"Authorization": f"Bearer {FLUTTERWAVE_SECRET_KEY}"}
    try:
        response = requests.get(
            f"{FLUTTERWAVE_BASE_URL}/transactions/verify_by_reference?tx_ref={tx_ref}",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        result = response.json()

        if (
            result.get("status") == "success"
            and result.get("data", {}).get("status") == "successful"
        ):
            return jsonify({"status": "success"})

        return jsonify({"status": "failed"}), 400

    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
