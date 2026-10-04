import os

import httpx
import pytest
from firebase_admin import auth

from app.db import get_db, notebook_path, user_path
from app.db.notebooks import DEMO_NOTEBOOK_ID
from app.storage import delete_prefix

# Set dummy environment variables ONLY if missing, preserving values provided via ../.env
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GEMINI_MODEL", "gemini-3.8-flash")
os.environ.setdefault("FIREBASE_PROJECT_ID", "demo-study-companion")
os.environ.setdefault("FIREBASE_STORAGE_BUCKET", "demo-study-companion.appspot.com")
os.environ.setdefault("JOBS_RUNNER_SECRET", "test-jobs-runner-secret")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")


@pytest.fixture(scope="session", autouse=True)
def check_emulator_preconditions():
    """Verify safety guards and emulator connectivity before running tests."""
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "")
    if not project_id.startswith("demo-"):
        pytest.fail(
            f"Safety violation: FIREBASE_PROJECT_ID='{project_id}' does not start with 'demo-'"
        )

    auth_host = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
    firestore_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
    storage_host = os.environ.get("STORAGE_EMULATOR_HOST")

    if not auth_host or not firestore_host or not storage_host:
        pytest.fail(
            f"Missing emulator host in environment: "
            f"FIREBASE_AUTH_EMULATOR_HOST={auth_host}, "
            f"FIRESTORE_EMULATOR_HOST={firestore_host}, "
            f"STORAGE_EMULATOR_HOST={storage_host}"
        )

    # Verify all three emulators are reachable
    try:
        res = httpx.get(f"http://{auth_host}/", timeout=2.0)
        assert res.status_code == 200
    except Exception as exc:
        pytest.fail(f"Auth emulator at {auth_host} is not running or unreachable: {exc}")

    try:
        res = httpx.get(f"http://{firestore_host}/", timeout=2.0)
        assert res.status_code == 200
    except Exception as exc:
        pytest.fail(f"Firestore emulator at {firestore_host} is not running or unreachable: {exc}")

    try:
        storage_url = storage_host if storage_host.startswith("http") else f"http://{storage_host}"
        res = httpx.get(f"{storage_url}/", timeout=2.0)
        assert res.status_code in (200, 501)
    except Exception as exc:
        pytest.fail(f"Storage emulator at {storage_host} is not running or unreachable: {exc}")


@pytest.fixture
def user_tracker():
    """Track created user UIDs and clean up only those specific users after the test."""
    created_uids = []
    yield created_uids

    db = get_db()
    for uid in created_uids:
        # Delete only test-created Firestore document
        try:
            db.document(user_path(uid)).delete()
        except Exception:
            pass
        # Delete test-created Auth user
        try:
            auth.delete_user(uid)
        except Exception:
            pass


@pytest.fixture
def notebook_tracker():
    """Track created notebook IDs and clean up only those specific notebooks and subcollections."""
    created_ids = []
    yield created_ids

    db = get_db()
    for nb in created_ids:
        try:
            nb_ref = db.document(notebook_path(nb))
            db.recursive_delete(nb_ref)
        except Exception:
            pass
        try:
            delete_prefix(f"notebooks/{nb}/")
        except Exception:
            pass


@pytest.fixture
def demo_notebook_context():
    """Backup any existing nb_demo_6041 before a test touches it, and restore/clean up after."""
    db = get_db()
    demo_ref = db.document(notebook_path(DEMO_NOTEBOOK_ID))
    existing_doc = demo_ref.get()
    had_existing = existing_doc.exists
    saved_data = existing_doc.to_dict() if had_existing else None

    yield

    try:
        if had_existing and saved_data is not None:
            demo_ref.set(saved_data)
        else:
            db.recursive_delete(demo_ref)
            delete_prefix(f"notebooks/{DEMO_NOTEBOOK_ID}/")
    except Exception:
        pass


def create_emulator_user(
    email: str | None = None,
    password: str = "password123",
) -> tuple[str, str]:
    """Create a user directly in the Auth emulator and return (uid, id_token)."""
    auth_host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"]
    url = f"http://{auth_host}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=fake-key"
    payload = {"returnSecureToken": True}
    if email:
        payload["email"] = email
        payload["password"] = password

    res = httpx.post(url, json=payload, timeout=5.0)
    assert res.status_code == 200, f"Failed to create emulator user: {res.text}"
    data = res.json()
    return data["localId"], data["idToken"]
