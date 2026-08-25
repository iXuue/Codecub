def resolve_context_window(response_value=None, configured_value=None):
    response_window = _positive_int(response_value)
    if response_window is not None:
        return {"context_window": response_window, "window_source": "provider_response", "quality": "operator_reported"}
    configured_window = _positive_int(configured_value)
    if configured_window is not None:
        return {"context_window": configured_window, "window_source": "user_configured", "quality": "user_configured"}
    return {"context_window": None, "window_source": "unavailable", "quality": "unavailable"}


def _positive_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
