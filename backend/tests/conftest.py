import os

# Set dummy environment variables ONLY if missing, preserving values provided via ../.env
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GEMINI_MODEL", "gemini-3.8-flash")
os.environ.setdefault("FIREBASE_PROJECT_ID", "demo-study-companion")
os.environ.setdefault("FIREBASE_STORAGE_BUCKET", "demo-study-companion.appspot.com")
os.environ.setdefault("JOBS_RUNNER_SECRET", "test-jobs-runner-secret")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
