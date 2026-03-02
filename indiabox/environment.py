"""
Environment callback for Unfold Admin.
Shows development/production badge in admin.
"""
import os


def environment_callback(request):
    """Return environment info for admin header."""
    if os.getenv('DEBUG', 'True').lower() == 'true':
        return ["Development", "warning"]
    return ["Production", "success"]
