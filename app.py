from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timedelta
import json
import os
import secrets
import socket
import ipaddress

from dotenv import load_dotenv

load_dotenv()

# QR code
try:
    import qrcode
except ImportError:
    qrcode = None

# Google OAuth
try:
    from authlib.integrations.flask_client import OAuth
except ImportError:
    OAuth = None


# --------------------------------------------------
# APP SETUP
# --------------------------------------------------

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "smart-attendance-secret-key-change-me"
)

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1
)


# --------------------------------------------------
# FILE LOCATIONS
# --------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(
    BASE_DIR,
    "data",
    "attendance.json"
)

QR_DIR = os.path.join(
    BASE_DIR,
    "static",
    "qr"
)

os.makedirs(QR_DIR, exist_ok=True)


# --------------------------------------------------
# DEFAULT DATA
# --------------------------------------------------

def default_data():
    return {
        "students": [],
        "attendance": [],
        "active_class": None
    }


# --------------------------------------------------
# LOAD DATA
# --------------------------------------------------

def load_data():

    if not os.path.exists(DATA_FILE):
        save_data(default_data())

    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

    except (json.JSONDecodeError, OSError):

        data = default_data()

    data.setdefault("students", [])
    data.setdefault("attendance", [])
    data.setdefault("active_class", None)

    return data


# --------------------------------------------------
# SAVE DATA
# --------------------------------------------------

def save_data(data):

    os.makedirs(
        os.path.dirname(DATA_FILE),
        exist_ok=True
    )

    with open(
        DATA_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=4
        )


# --------------------------------------------------
# FIND LAPTOP LOCAL IP
# --------------------------------------------------

def get_local_ip():

    """
    Finds the local IPv4 address of the teacher's computer.
    This is the address that the student's phone uses
    to open the attendance page.
    """

    try:

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        sock.connect(
            ("8.8.8.8", 80)
        )

        ip = sock.getsockname()[0]

        sock.close()

        return ip

    except Exception:

        try:

            return socket.gethostbyname(
                socket.gethostname()
            )

        except Exception:

            return "127.0.0.1"


# --------------------------------------------------
# SAME WIFI CHECK
# --------------------------------------------------

def same_network(client_ip, server_ip):

    """
    Checks whether the student's phone and
    teacher's computer appear to be on the
    same private Wi-Fi network.

    This is more flexible than the old strict /24 check.
    """

    try:

        client = ipaddress.ip_address(client_ip)
        server = ipaddress.ip_address(server_ip)

        # Only IPv4 is used for this classroom check.
        if client.version != 4 or server.version != 4:
            return False

        client_parts = client_ip.split(".")
        server_parts = server_ip.split(".")

        if len(client_parts) != 4:
            return False

        if len(server_parts) != 4:
            return False


        # ------------------------------------------
        # 192.168.x.x
        # ------------------------------------------

        if (
            client_parts[0] == "192"
            and client_parts[1] == "168"
            and server_parts[0] == "192"
            and server_parts[1] == "168"
        ):

            return (
                client_parts[2] == server_parts[2]
            )


        # ------------------------------------------
        # 10.x.x.x
        # ------------------------------------------

        if (
            client_parts[0] == "10"
            and server_parts[0] == "10"
        ):

            return (
                client_parts[1] == server_parts[1]
            )


        # ------------------------------------------
        # 172.16.x.x - 172.31.x.x
        # ------------------------------------------

        if (
            client_parts[0] == "172"
            and server_parts[0] == "172"
        ):

            client_second = int(client_parts[1])
            server_second = int(server_parts[1])

            if (
                16 <= client_second <= 31
                and 16 <= server_second <= 31
            ):

                return (
                    client_second == server_second
                )


        # ------------------------------------------
        # Fallback /24 check
        # ------------------------------------------

        network = ipaddress.ip_network(
            f"{server_ip}/24",
            strict=False
        )

        return client in network

    except Exception:

        return False


# --------------------------------------------------
# CLIENT IP
# --------------------------------------------------

def get_client_ip():
    # Cloudflare Tunnel sends the real visitor IP in this header.
    cloudflare_ip = request.headers.get("CF-Connecting-IP")

    if cloudflare_ip:
        return cloudflare_ip.strip()

    return request.remote_addr or ""


def get_public_ip():
    """Get the public internet IP of the teacher computer/network."""
    try:
        import urllib.request

        with urllib.request.urlopen(
            "https://api.ipify.org",
            timeout=5
        ) as response:
            return response.read().decode().strip()

    except Exception:
        return ""


# --------------------------------------------------
# ATTENDANCE STATISTICS
# --------------------------------------------------

def calculate_stats(student_id, data=None):

    if data is None:
        data = load_data()

    records = [
        record
        for record in data["attendance"]
        if record.get("student_id") == student_id
    ]

    total = len(records)

    present = sum(
        1
        for record in records
        if str(
            record.get("status", "")
        ).lower() == "present"
    )

    absent = total - present

    if total > 0:
        percentage = round(
            (present / total) * 100,
            1
        )
    else:
        percentage = 0.0

    return {
        "total": total,
        "present": present,
        "absent": absent,
        "percentage": percentage
    }


# --------------------------------------------------
# GOOGLE OAUTH
# --------------------------------------------------

oauth = None

if OAuth:

    oauth = OAuth(app)

    google_client_id = os.environ.get(
        "GOOGLE_CLIENT_ID",
        ""
    )

    google_client_secret = os.environ.get(
        "GOOGLE_CLIENT_SECRET",
        ""
    )

    if google_client_id and google_client_secret:

        oauth.register(

            name="google",

            client_id=google_client_id,

            client_secret=google_client_secret,

            server_metadata_url=(
                "https://accounts.google.com/"
                ".well-known/openid-configuration"
            ),

            client_kwargs={
                "scope": "openid email profile"
            }
        )


# --------------------------------------------------
# GOOGLE ENABLED?
# --------------------------------------------------

def google_enabled():

    if oauth is None:
        return False

    try:

        google = oauth.create_client("google")
        return google is not None

    except Exception:

        return False


# --------------------------------------------------
# CURRENT USER
# --------------------------------------------------

def current_user():

    return session.get("user")


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/")
def dashboard():

    data = load_data()

    students_view = []

    for student in data["students"]:

        stats = calculate_stats(
            student["id"],
            data
        )

        students_view.append({
            **student,
            **stats
        })


    total_students = len(
        data["students"]
    )


    total_classes = len(
        data["attendance"]
    )


    present_count = sum(
        1
        for record in data["attendance"]
        if str(
            record.get("status", "")
        ).lower() == "present"
    )


    if total_classes > 0:

        average = round(
            (present_count / total_classes) * 100,
            1
        )

    else:

        average = 0.0


    return render_template(

        "dashboard.html",

        students=students_view,

        total_students=total_students,

        average_attendance=average,

        safe_students=sum(
            1
            for student in students_view
            if student["percentage"] >= 75
        ),

        at_risk=sum(
            1
            for student in students_view
            if student["percentage"] < 75
        ),

        active_class=data.get(
            "active_class"
        )
    )


# --------------------------------------------------
# STUDENTS
# --------------------------------------------------

@app.route("/students")
def students():

    data = load_data()

    students_view = []

    for student in data["students"]:

        students_view.append({

            **student,

            **calculate_stats(
                student["id"],
                data
            )

        })


    return render_template(
        "students.html",
        students=students_view
    )


# --------------------------------------------------
# ADD STUDENT
# --------------------------------------------------

@app.route(
    "/add-student",
    methods=["GET", "POST"]
)
def add_student():

    if request.method == "POST":

        data = load_data()

        name = request.form.get(
            "name",
            ""
        ).strip()

        roll = request.form.get(
            "roll",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()


        if not name or not roll or not email:

            flash(
                "Please fill all student details.",
                "error"
            )

            return redirect(
                url_for("add_student")
            )


        # Duplicate roll number
        for student in data["students"]:

            if student.get(
                "roll",
                ""
            ).lower() == roll.lower():

                flash(
                    "Roll number already exists.",
                    "error"
                )

                return redirect(
                    url_for("add_student")
                )


        # Duplicate Gmail
        for student in data["students"]:

            if student.get(
                "email",
                ""
            ).lower() == email:

                flash(
                    "This Gmail is already assigned to a student.",
                    "error"
                )

                return redirect(
                    url_for("add_student")
                )


        # New ID
        new_id = max(
            [
                student["id"]
                for student in data["students"]
            ],
            default=0
        ) + 1


        data["students"].append({

            "id": new_id,

            "name": name,

            "roll": roll,

            "email": email

        })


        save_data(data)


        flash(
            "Student added successfully.",
            "success"
        )


        return redirect(
            url_for("students")
        )


    return render_template(
        "add_student.html"
    )


# --------------------------------------------------
# DELETE STUDENT
# --------------------------------------------------

@app.route(
    "/delete-student/<int:student_id>",
    methods=["POST"]
)
def delete_student(student_id):

    data = load_data()


    data["students"] = [

        student

        for student in data["students"]

        if student["id"] != student_id

    ]


    data["attendance"] = [

        record

        for record in data["attendance"]

        if record.get(
            "student_id"
        ) != student_id

    ]


    save_data(data)


    flash(
        "Student deleted.",
        "success"
    )


    return redirect(
        url_for("students")
    )


# --------------------------------------------------
# ATTENDANCE PAGE
# --------------------------------------------------

@app.route("/attendance")
def attendance():

    data = load_data()

    return render_template(

        "attendance.html",

        students=data["students"],

        active_class=data.get(
            "active_class"
        ),

        local_ip=get_local_ip()

    )


# --------------------------------------------------
# GENERATE QR
# --------------------------------------------------

@app.route(
    "/generate-qr",
    methods=["POST"]
)
def generate_qr():

    if qrcode is None:

        flash(
            "QR package is missing. Run: pip install qrcode[pil]",
            "error"
        )

        return redirect(
            url_for("attendance")
        )


    subject = request.form.get(
        "subject",
        ""
    ).strip()


    room = request.form.get(
        "room",
        ""
    ).strip()


    duration_text = request.form.get(
        "duration",
        "10"
    ).strip()


    if not subject or not room:

        flash(
            "Enter subject and classroom.",
            "error"
        )

        return redirect(
            url_for("attendance")
        )


    try:

        duration = int(
            duration_text
        )

        duration = max(
            1,
            min(60, duration)
        )

    except ValueError:

        duration = 10


    # Unique session token
    token = secrets.token_urlsafe(18)


    expires = (
        datetime.now()
        + timedelta(minutes=duration)
    )


    teacher_ip = get_local_ip()
    teacher_public_ip = get_public_ip()


    data = load_data()


    # Create active class
    data["active_class"] = {

        "token": token,

        "subject": subject,

        "room": room,

        "teacher_ip": teacher_ip,
        "teacher_public_ip": teacher_public_ip,

        "created_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "expires_at": expires.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "duration": duration

    }


    save_data(data)


    # URL for student's phone.
    # Use the HTTPS Cloudflare URL when configured because
    # Google OAuth does not allow a raw private IP redirect.
    public_url = os.environ.get(
        "PUBLIC_URL",
        f"http://{teacher_ip}:5000"
    ).rstrip("/")

    scan_url = f"{public_url}/scan/{token}"


    qr_path = os.path.join(
        QR_DIR,
        "attendance_qr.png"
    )


    try:

        img = qrcode.make(
            scan_url
        )

        img.save(qr_path)

    except Exception:

        flash(
            "QR code could not be generated.",
            "error"
        )

        return redirect(
            url_for("attendance")
        )


    return render_template(

        "qr.html",

        active_class=data[
            "active_class"
        ],

        scan_url=scan_url

    )


# --------------------------------------------------
# CLOSE CLASS
# --------------------------------------------------

@app.route(
    "/close-class",
    methods=["POST"]
)
def close_class():

    data = load_data()

    data["active_class"] = None

    save_data(data)


    qr_file = os.path.join(
        QR_DIR,
        "attendance_qr.png"
    )


    if os.path.exists(qr_file):

        try:

            os.remove(qr_file)

        except OSError:

            pass


    flash(
        "Attendance session closed.",
        "success"
    )


    return redirect(
        url_for("attendance")
    )


# --------------------------------------------------
# CHECK ACTIVE CLASS
# --------------------------------------------------

def active_class_valid(active_class):

    if not active_class:
        return False


    try:

        expiry = datetime.strptime(

            active_class["expires_at"],

            "%Y-%m-%d %H:%M:%S"

        )

        return datetime.now() <= expiry


    except (
        KeyError,
        ValueError
    ):

        return False


# --------------------------------------------------
# SCAN QR
# --------------------------------------------------

@app.route("/scan/<token>")
def scan(token):

    data = load_data()

    active = data.get(
        "active_class"
    )


    # ------------------------------------------
    # Check QR session
    # ------------------------------------------

    if (
        not active
        or active.get("token") != token
    ):

        return render_template(

            "verification.html",

            allowed=False,

            title="Attendance Not Available",

            message=(
                "This QR code is no longer active."
            )

        )


    # ------------------------------------------
    # Check expiry
    # ------------------------------------------

    if not active_class_valid(active):

        data["active_class"] = None

        save_data(data)


        return render_template(

            "verification.html",

            allowed=False,

            title="QR Code Expired",

            message=(
                "The attendance session has expired. "
                "Ask the teacher to generate a new QR code."
            )

        )


    # ------------------------------------------
    # Get phone IP
    # ------------------------------------------

    client_ip = get_client_ip()

    teacher_public_ip = active.get(
        "teacher_public_ip",
        ""
    )


    # ------------------------------------------
    # SAME NETWORK CHECK
    # ------------------------------------------
    # With Cloudflare Tunnel, the phone's private Wi-Fi IP
    # is not visible to Flask. We therefore compare the
    # public internet IP of the phone and teacher network.
    # On normal Wi-Fi this is the same public IP for both.

    if not teacher_public_ip or client_ip != teacher_public_ip:

        return render_template(

            "verification.html",

            allowed=False,

            title="Attendance Not Available",

            message=(
                "Access denied. Your phone does not "
                "appear to be on the same local Wi-Fi "
                "network as the teacher's computer."
            ),

            details=(
                f"Phone public IP: {client_ip or 'unknown'} | "
                f"Teacher public IP: {teacher_public_ip or 'unknown'}"
            )

        )


    # ------------------------------------------
    # Google login
    # ------------------------------------------

    if not current_user():

        if google_enabled():

            session["pending_token"] = token

            return redirect(
                url_for("google_login")
            )


        return render_template(

            "verification.html",

            allowed=False,

            title="Google Login Required",

            message=(
                "Google verification is not configured yet. "
                "The teacher must configure Google login "
                "before students can mark attendance."
            )

        )


    # ------------------------------------------
    # Get verified Google email
    # ------------------------------------------

    email = (
        current_user()
        .get("email", "")
        .lower()
    )


    # ------------------------------------------
    # Find student by Gmail
    # ------------------------------------------

    student = next(

        (

            student

            for student in data["students"]

            if student.get(
                "email",
                ""
            ).lower() == email

        ),

        None

    )


    # ------------------------------------------
    # Gmail not registered
    # ------------------------------------------

    if not student:

        return render_template(

            "verification.html",

            allowed=False,

            title="Student Not Registered",

            message=(
                "This Google account is not assigned "
                "to any student in Smart Attendance Tracker."
            )

        )


    # ------------------------------------------
    # Prevent duplicate attendance
    # ------------------------------------------

    already_marked = any(

        record.get("student_id")
        == student["id"]

        and record.get("session_token")
        == token

        for record in data["attendance"]

    )


    if already_marked:

        return render_template(

            "verification.html",

            allowed=False,

            title="Attendance Already Marked",

            message=(
                "Your attendance has already been "
                "marked for this class."
            ),

            student=student

        )


    # ------------------------------------------
    # Mark attendance
    # ------------------------------------------

    data["attendance"].append({

        "student_id":
            student["id"],

        "subject":
            active["subject"],

        "room":
            active["room"],

        "date":
            datetime.now().strftime(
                "%Y-%m-%d"
            ),

        "time":
            datetime.now().strftime(
                "%H:%M:%S"
            ),

        "status":
            "Present",

        "session_token":
            token,

        "verified_email":
            email,

        "verified_ip":
            client_ip

    })


    save_data(data)


    # ------------------------------------------
    # Success
    # ------------------------------------------

    return render_template(

        "verification.html",

        allowed=True,

        title="Attendance Marked",

        message=(
            f"Attendance successfully marked "
            f"for {student['name']}."
        ),

        student=student,

        active_class=active

    )


# --------------------------------------------------
# GOOGLE LOGIN
# --------------------------------------------------

@app.route("/login/google")
def google_login():

    if not google_enabled():

        return render_template(

            "verification.html",

            allowed=False,

            title="Google Login Not Configured",

            message=(
                "Google login has not been configured "
                "for this application yet."
            )

        )


    redirect_uri = url_for(
        "google_callback",
        _external=True
    )


    return oauth.google.authorize_redirect(
        redirect_uri
    )


# --------------------------------------------------
# GOOGLE CALLBACK
# --------------------------------------------------

@app.route("/auth/google/callback")
def google_callback():

    if not google_enabled():

        abort(503)


    try:

        token = (
            oauth.google
            .authorize_access_token()
        )


        userinfo = token.get(
            "userinfo"
        )


        if not userinfo:

            userinfo = (
                oauth.google
                .userinfo()
            )


        email = (
            userinfo.get(
                "email"
            )
            or ""
        ).lower()


        name = (
            userinfo.get(
                "name"
            )
            or email.split("@")[0]
        )


        # Store verified Google user
        session["user"] = {

            "email": email,

            "name": name

        }


        pending_token = session.pop(
            "pending_token",
            None
        )


        # Return to QR attendance
        if pending_token:

            return redirect(

                url_for(
                    "scan",
                    token=pending_token
                )

            )


        return redirect(
            url_for("dashboard")
        )


    except Exception:

        return render_template(

            "verification.html",

            allowed=False,

            title="Google Verification Failed",

            message=(
                "Google verification could not be "
                "completed. Check the Google login "
                "configuration."
            )

        )


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------

@app.route("/logout")
def logout():

    session.pop(
        "user",
        None
    )

    return redirect(
        url_for("dashboard")
    )


# --------------------------------------------------
# ATTENDANCE ADVISOR
# --------------------------------------------------

@app.route("/advisor")
def advisor():

    return render_template(
        "advisor.html"
    )


# --------------------------------------------------
# CALCULATE ADVISOR
# --------------------------------------------------

@app.route(
    "/calculate-advisor",
    methods=["POST"]
)
def calculate_advisor():

    try:

        present = int(
            request.form.get(
                "present",
                0
            )
        )


        total = int(
            request.form.get(
                "total",
                0
            )
        )


        target = float(
            request.form.get(
                "target",
                75
            )
        )


        # Validate input
        if (
            present < 0
            or total < 0
            or present > total
            or target <= 0
            or target > 100
        ):

            raise ValueError


        # Current attendance
        if total > 0:

            current = round(
                (present / total) * 100,
                1
            )

        else:

            current = 0.0


        # --------------------------------------
        # Already above target
        # --------------------------------------

        if current >= target:

            can_miss = 0

            t = total


            while (

                (present / (t + 1)) * 100
                >= target

            ):

                t += 1

                can_miss += 1


                if can_miss >= 10000:

                    break


            result = (

                f"You can miss approximately "
                f"{can_miss} more class(es) and "
                f"stay at or above {target:g}%."

            )


        # --------------------------------------
        # Below target
        # --------------------------------------

        else:

            needed = 0

            p = present

            t = total


            while (

                t > 0
                and (p / t) * 100 < target
                and needed < 10000

            ):

                p += 1

                t += 1

                needed += 1


            # If there were zero classes
            if total == 0:

                needed = 1


            result = (

                f"You need to attend the next "
                f"{needed} consecutive class(es) "
                f"to reach about {target:g}%."

            )


        return render_template(

            "advisor.html",

            result=result,

            current=current,

            present=present,

            total=total,

            target=target

        )


    except ValueError:

        flash(
            "Enter valid attendance numbers.",
            "error"
        )

        return redirect(
            url_for("advisor")
        )


# --------------------------------------------------
# STUDENT REPORT
# --------------------------------------------------

@app.route(
    "/student/<int:student_id>"
)
def student_report(student_id):

    data = load_data()


    # Find student
    student = next(

        (

            student

            for student in data["students"]

            if student["id"] == student_id

        ),

        None

    )


    if not student:

        abort(404)


    # Overall statistics
    stats = calculate_stats(
        student_id,
        data
    )


    # Attendance records
    records = [

        record

        for record in data["attendance"]

        if record.get(
            "student_id"
        ) == student_id

    ]


    # Newest first
    records.sort(

        key=lambda record: (

            record.get(
                "date",
                ""
            ),

            record.get(
                "time",
                ""
            )

        ),

        reverse=True

    )


    # ------------------------------------------
    # Subject-wise data
    # ------------------------------------------

    subjects = {}


    for record in records:

        subject = record.get(
            "subject",
            "Unknown"
        )


        if subject not in subjects:

            subjects[subject] = {

                "present": 0,

                "total": 0

            }


        subjects[subject]["total"] += 1


        if str(
            record.get(
                "status",
                ""
            )
        ).lower() == "present":

            subjects[subject]["present"] += 1


    subject_rows = []


    for subject, values in subjects.items():

        if values["total"] > 0:

            percentage = round(

                (
                    values["present"]
                    / values["total"]
                ) * 100,

                1

            )

        else:

            percentage = 0


        if percentage >= 75:

            status = "SAFE"

        else:

            status = "AT RISK"


        subject_rows.append({

            "subject":
                subject,

            "present":
                values["present"],

            "total":
                values["total"],

            "percentage":
                percentage,

            "status":
                status

        })


    return render_template(

        "student_report.html",

        student=student,

        stats=stats,

        subjects=subject_rows,

        history=records

    )


# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":

    print()
    print("------------------------------------------")
    print(" SMART ATTENDANCE TRACKER")
    print("------------------------------------------")
    print()
    print(
        "Teacher computer IP:",
        get_local_ip()
    )
    print()
    print(
        "Open on this computer:"
    )
    print(
        "http://127.0.0.1:5000"
    )
    print()
    print(
        "Phone should use:"
    )
    print(
        f"http://{get_local_ip()}:5000"
    )
    print()
    print("------------------------------------------")
    print()


    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True

    )