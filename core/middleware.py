from django.shortcuts import redirect
from django.urls import reverse
import re

"""
DJANGO MIDDLEWARE: THE REQUEST INTERCEPTOR
------------------------------------------
Middleware is a framework of hooks into Django's request/response processing.
It's a light, low-level "plugin" system for globally altering Django's
input or output.

Think of it as a series of 'Checkpoints' that every request must pass through
before it reaches a View, and every response must pass through before
it reaches the user's browser.

In SAIS, we use Middleware for:
1. STORE CONTEXT: Automatically identifying which store the user is working in.
2. SECURITY: Adding headers like CSP (Content Security Policy).
3. SANITIZATION: Cleaning up hardware IDs before they enter the system.
"""

class StoreContextMiddleware:
    """
    MULTI-TENANT CONTEXT MANAGER
    ----------------------------
    This is the most important middleware in the project.
    It reads the 'active_store_id' from the user's session and attaches the
    actual Store object to the 'request' variable.

    This makes 'request.active_store' available in every View and Template.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Initialize context variables so they are always present, even if empty
        request.active_store = None
        request.active_company = None

        # PERFORMANCE: Skip this logic for static files, media, or logout paths
        bypass_paths = ['/admin/logout/', '/static/', '/media/', '/set-store/', '/help/']
        if any(request.path.startswith(p) for p in bypass_paths):
            return self.get_response(request)

        if request.user.is_authenticated:
            # Look up the ID previously saved by the 'set_active_store' view
            active_store_id = request.session.get('active_store_id')

            # --- CASE A: SUPERUSER ---
            if request.user.is_superuser:
                if active_store_id:
                    from .models import Store
                    # select_related performs a JOIN to get the Company too
                    store = Store.objects.select_related('company').filter(id=active_store_id).first()
                    if store:
                        request.active_store = store
                        request.active_company = store.company
                
                # FORCE SELECTION: If an admin tries to view data without picking a store,
                # send them back to the picker.
                if not request.active_store and 'admin' in request.path and request.path != reverse('select_store'):
                    return redirect('select_store')
                
                return self.get_response(request)

            # --- CASE B: REGULAR USER (Company Staff) ---
            user_company = getattr(request.user, 'company', None)

            if user_company:
                allowed_stores = request.user.managed_stores.all()
                store_count = allowed_stores.count()

                # AUTO-SELECT: If the user only has access to 1 store, pick it for them.
                if store_count == 1:
                    request.active_store = allowed_stores.first()
                    request.active_company = user_company
                    request.session['active_store_id'] = request.active_store.id
                
                # SELECTION CHECK: If they have a selection in their session, verify it's valid.
                elif active_store_id:
                    request.active_store = allowed_stores.filter(id=active_store_id).first()
                    if request.active_store:
                        request.active_company = user_company
                    else:
                        # SECURITY: They tried to access a store they aren't assigned to!
                        if 'active_store_id' in request.session:
                            del request.session['active_store_id']
                        return redirect('select_store')
                        
                # REQUIRE SELECTION: They have multiple stores but haven't picked one yet.
                elif store_count > 1:
                    if 'admin' in request.path and request.path != reverse('select_store'):
                        return redirect('select_store')
                        
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """
    DEFENSE-IN-DEPTH SECURITY
    -------------------------
    Injects standard security headers into every HTTP response.
    This protects the system against XSS, Clickjacking, and other common attacks.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        
        # CSP (Content Security Policy): Tells the browser which scripts/styles are safe to run.
        # This prevents hackers from injecting malicious scripts into our pages.
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'", # 'unsafe-inline' is needed for some Django Admin features
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self'",
            "connect-src 'self'",
            "frame-ancestors 'none'", # Prevents the site from being put in an <iframe> (Anti-Clickjacking)
            "form-action 'self'",
            "base-uri 'self'",
        ]
        response['Content-Security-Policy'] = "; ".join(csp_directives)
        
        # Nosniff: Prevents the browser from trying to "guess" the content type (Security risk)
        response['X-Content-Type-Options'] = 'nosniff'

        # Deny Frames: Another layer of Anti-Clickjacking
        response['X-Frame-Options'] = 'DENY'

        # Referrer Policy: Controls how much info is sent when clicking links to other sites
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        return response


class InputSanitizationMiddleware:
    """
    DATA HYGIENE (Hardware IDs)
    ---------------------------
    ESL Tag MAC addresses (or Serials) come in many formats:
    "BE:01:02:03", "BE-01-02-03", or "BE010203".

    This utility ensures we always store them as "BE010203" (clean alphanumeric).
    """
    @classmethod
    def sanitize_tag_id(cls, raw_id):
        """
        Removes all punctuation and spaces, converting the ID to a clean,
        uppercase string.
        """
        if not raw_id:
            return None

        # Regex: Keep only numbers 0-9 and letters A-Z
        cleaned = re.sub(r'[^0-9A-Za-z]', '', str(raw_id).strip())

        # Business Rule: Physical IDs are typically between 8 and 15 characters.
        if 8 <= len(cleaned) <= 15:
            return cleaned.upper()

        return None

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)
