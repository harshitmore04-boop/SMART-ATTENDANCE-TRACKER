# Smart Attendance Tracker

A B.Tech CSE Python mini-project using Flask, JSON, QR code and optional Google OAuth.

## Main features

1. Teacher adds students and assigns each student a Gmail address.
2. Teacher enters subject, room and QR validity time.
3. A temporary QR code is generated.
4. QR contains the teacher computer's LAN address.
5. Phone must be on the same local network.
6. Student identity is taken from Google login instead of typing email/roll number.
7. The Gmail must match the Gmail assigned to that student.
8. One student can mark attendance only once for a QR session.
9. Dashboard, student reports and Attendance Advisor are included.

## Install

Open terminal inside this project folder:

    python -m pip install -r requirements.txt

Run:

    python app.py

Teacher computer:

    http://127.0.0.1:5000

Phone:

Use the LAN address shown by the QR page, for example:

    http://192.168.1.5:5000

## Google login setup

Automatic Gmail identification requires Google OAuth.

Set these environment variables before running:

Windows PowerShell:

    $env:GOOGLE_CLIENT_ID="YOUR_CLIENT_ID"
    $env:GOOGLE_CLIENT_SECRET="YOUR_CLIENT_SECRET"

Then run:

    python app.py

For a local project, add this authorized redirect URI in Google Cloud:

    http://YOUR_LAN_IP:5000/auth/google/callback

Example:

    http://192.168.1.5:5000/auth/google/callback

Do not put a Google client secret inside the HTML or JavaScript.

## Important network note

The same-Wi-Fi check is designed for a normal classroom LAN. Some college Wi-Fi networks use client isolation, which prevents phones from reaching another device even when both appear on the same Wi-Fi. In that case the network/router must allow local device-to-device communication.

Also allow Python through Windows Firewall for Private networks if the phone cannot open the QR link.
