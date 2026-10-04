import os
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials, firestore
from google.auth.credentials import AnonymousCredentials

from app.config import get_settings


class EmulatorCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


def init_firebase() -> firebase_admin.App:
    """Initialize Firebase Admin SDK once with safety checks."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    settings = get_settings()
    project_id = os.environ.get("FIREBASE_PROJECT_ID") or settings.firebase_project_id
    firestore_emu = os.environ.get("FIRESTORE_EMULATOR_HOST")
    auth_emu = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")

    # Safety check: demo project IDs must be backed by emulators
    if project_id and project_id.startswith("demo-"):
        if not firestore_emu or not auth_emu:
            raise RuntimeError(
                f"Safety check failed: FIREBASE_PROJECT_ID '{project_id}' starts with 'demo-', "
                f"but FIRESTORE_EMULATOR_HOST={firestore_emu} or "
                f"FIREBASE_AUTH_EMULATOR_HOST={auth_emu} is missing. "
                "Refusing to run to prevent reaching production Firestore."
            )

    options = {"projectId": project_id}
    if settings.google_application_credentials:
        cred = credentials.Certificate(settings.google_application_credentials)
    elif firestore_emu or auth_emu:
        cred = EmulatorCredential()
    else:
        cred = None

    return firebase_admin.initialize_app(credential=cred, options=options)


@lru_cache
def get_db() -> firestore.Client:
    """Return cached Firestore client."""
    init_firebase()
    return firestore.client()
