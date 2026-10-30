def log_activity(user, action, object_type="", object_id="", description=""):
    """Record who did what. Never raises — logging must not break the action itself."""
    from .models import ActivityLog

    try:
        ActivityLog.objects.create(
            user=user if (user and getattr(user, "is_authenticated", False)) else None,
            action=action,
            object_type=object_type,
            object_id=str(object_id or ""),
            description=description[:500],
        )
    except Exception:  # pragma: no cover
        pass
