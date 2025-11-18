# =============================================================================
# FILE: Procfile
# Render deployment configuration
# =============================================================================

web: gunicorn main:app --workers 1 --bind 0.0.0.0:$PORT --timeout 120 --log-level info
