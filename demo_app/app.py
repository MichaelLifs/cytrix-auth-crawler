"""Local demo authenticated app for CYTRIX phases."""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable, TypeVar

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

load_dotenv()

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("DEMO_APP_SECRET_KEY", "change-me-in-local-env")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DEMO_EMAIL = os.getenv("DEMO_APP_EMAIL", "admin@example.com")
DEMO_PASSWORD = os.getenv("DEMO_APP_PASSWORD", "Password123!")

_F = TypeVar("_F", bound=Callable[..., Any])


def login_required(view: _F) -> _F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def api_login_required(view: _F) -> _F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not session.get("authenticated"):
            return jsonify({"error": "unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


@app.get("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        if email == DEMO_EMAIL and password == DEMO_PASSWORD:
            session["authenticated"] = True
            session["user_email"] = email
            return redirect(url_for("dashboard"))
        error = "Invalid password or email."

    return render_template_string(
        """
        <!doctype html>
        <html>
          <body>
            <h1>Demo Login</h1>
            {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
            <form method="post">
              <label for="email">Email</label>
              <input id="email" name="email" type="email" required />
              <label for="password">Password</label>
              <input id="password" name="password" type="password" required />
              <button type="submit">Sign in</button>
            </form>
          </body>
        </html>
        """,
        error=error,
    )


@app.get("/dashboard")
@login_required
def dashboard():
    return render_template_string(
        """
        <!doctype html>
        <html>
          <head>
            <title>Demo dashboard</title>
            <link rel="stylesheet" href="{{ url_for('static_style') }}" />
          </head>
          <body>
            <h1>Dashboard</h1>
            <p>Welcome, {{ email }}</p>
            <ul>
              <li><a href="{{ url_for('profile') }}">Profile</a></li>
              <li><a href="{{ url_for('settings') }}">Settings</a></li>
              <li><a href="{{ url_for('logout') }}">Logout</a></li>
              <li><a href="{{ url_for('delete_account') }}">Delete account</a></li>
            </ul>
            <script src="{{ url_for('static_app_js') }}"></script>
          </body>
        </html>
        """,
        email=session.get("user_email", "admin@example.com"),
    )


@app.get("/profile")
@login_required
def profile():
    return render_template_string(
        """
        <!doctype html>
        <html>
          <head>
            <title>Demo profile</title>
            <meta name="description" content="CYTRIX demo profile page for crawler fixtures." />
            <link rel="stylesheet" href="{{ url_for('static_style') }}" />
          </head>
          <body>
            <h1>Profile</h1>
            <h2>Account overview</h2>
            <h3>Demo section</h3>
            <p>Email: {{ email }}</p>
            <script src="https://example.com/external-demo.js" defer></script>
            <form method="post" action="{{ url_for('profile') }}">
              <input type="hidden" name="_token" value="demo-csrf-token" />
              <label for="nickname">Nickname</label>
              <input id="nickname" name="nickname" type="text" />
              <button type="submit">Save profile</button>
              <button type="button">Preview</button>
            </form>
            <p><a href="/profile/security">Security settings</a></p>
            <a href="{{ url_for('dashboard') }}">Back to dashboard</a>
            <script src="{{ url_for('static_app_js') }}"></script>
            <script>
              fetch("{{ url_for('api_profile') }}", { headers: { "Accept": "application/json" } })
                .then((r) => r.json())
                .catch(() => null);
            </script>
          </body>
        </html>
        """,
        email=session.get("user_email", "admin@example.com"),
    )


@app.get("/profile/security")
@login_required
def profile_security():
    return render_template_string(
        """
        <!doctype html>
        <html>
          <head>
            <title>Demo security settings</title>
            <meta name="description" content="CYTRIX demo profile security page (depth 2)." />
            <link rel="stylesheet" href="{{ url_for('static_style') }}" />
          </head>
          <body>
            <h1>Security settings</h1>
            <p>Two-factor and session options (demo placeholder).</p>
            <a href="{{ url_for('profile') }}">Back to profile</a>
            <script src="{{ url_for('static_app_js') }}"></script>
          </body>
        </html>
        """
    )


@app.get("/settings")
@login_required
def settings():
    return render_template_string(
        """
        <!doctype html>
        <html>
          <head>
            <title>Demo settings</title>
            <meta name="description" content="CYTRIX demo settings page." />
            <link rel="stylesheet" href="{{ url_for('static_style') }}" />
          </head>
          <body>
            <h1>Settings</h1>
            <h2>Preferences</h2>
            <p>Deterministic demo settings page.</p>
            <a href="{{ url_for('dashboard') }}">Back to dashboard</a>
            <script src="{{ url_for('static_app_js') }}"></script>
            <script>
              fetch("{{ url_for('api_settings') }}", { headers: { "Accept": "application/json" } })
                .then((r) => r.json())
                .catch(() => null);
            </script>
          </body>
        </html>
        """
    )


@app.get("/api/profile")
@api_login_required
def api_profile():
    return jsonify(
        {
            "email": session.get("user_email", DEMO_EMAIL),
            "role": "demo-admin",
            "preferences": {"theme": "light", "notifications": True},
        }
    )


@app.get("/api/settings")
@api_login_required
def api_settings():
    return jsonify(
        {
            "version": "1.0.0",
            "feature_flags": {"beta_ui": False, "experiments": True},
        }
    )


@app.get("/static/app.js")
def static_app_js():
    return (
        "// Demo deterministic JS asset for CYTRIX crawler fixtures.\n"
        "window.__cytrixDemo = { ready: true };\n",
        200,
        {"Content-Type": "application/javascript; charset=utf-8"},
    )


@app.get("/static/style.css")
def static_style():
    return (
        "body { font-family: sans-serif; margin: 1.5rem; }\n"
        "h1, h2, h3 { color: #222; }\n",
        200,
        {"Content-Type": "text/css; charset=utf-8"},
    )


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/delete-account")
def delete_account():
    session.clear()
    return render_template_string(
        """
        <!doctype html>
        <html>
          <body>
            <h1>Account deleted (demo)</h1>
            <a href="{{ url_for('login') }}">Return to login</a>
          </body>
        </html>
        """
    )


if __name__ == "__main__":
    app.run(
        host=os.getenv("DEMO_APP_HOST", "127.0.0.1"),
        port=int(os.getenv("DEMO_APP_PORT", "8000")),
        debug=False,
    )
