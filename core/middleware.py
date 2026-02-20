from django.shortcuts import redirect
from django.urls import reverse
from django.http import HttpResponse
import re

#class StoreContextMiddleware:
#    def __init__(self, get_response):
#        self.get_response = get_response
#
#    def __call__(self, request):
#        # Initialize our "Global Variables" as None
#        request.active_store = None
#        request.active_company = None
#
#        # Bypass for static files, logout, and the setter view itself
#        if any(request.path.startswith(p) for p in ['/admin/logout/', '/static/', '/set-store/']):
#            return self.get_response(request)
#
#        if request.user.is_authenticated:
#            active_store_id = request.session.get('active_store_id')
#
#            # --- 1. SUPERUSER LOGIC (The "God Mode" bypass) ---
#            if request.user.is_superuser:
#                if active_store_id:
#                    from .models import Store
#                    # Use select_related to get the company in one database query
#                    store = Store.objects.select_related('company').filter(id=active_store_id).first()
#                    if store:
#                        request.active_store = store
#                        request.active_company = store.company
#                
#                # If a superuser is in Admin but hasn't picked a store, send them to the list
#                if not request.active_store and 'admin' in request.path and request.path != reverse('select_store'):
#                    return redirect('select_store')
#                
#                return self.get_response(request)
#
#            # --- 2. REGULAR USER LOGIC (Owners & Managers) ---
#                        
#            user_company = getattr(request.user, 'company', None)
#
#            if user_company:
#                # Get ONLY the stores explicitly assigned to this user in the Admin
#                allowed_stores = request.user.managed_stores.all()
#                store_count = allowed_stores.count()
#
#                # Case A: Only 1 assigned store -> Set it automatically
#                if store_count == 1:
#                    request.active_store = allowed_stores.first()
#                    request.active_company = user_company
#                    request.session['active_store_id'] = request.active_store.id
#                
#                # Case B: Multiple assigned stores, one is already in the session
#                elif active_store_id:
#                    # We check allowed_stores for BOTH owners and managers now.
#                    # This respects the 'Managed Stores' selection you just fixed.
#                    request.active_store = allowed_stores.filter(id=active_store_id).first()
#
#                    if request.active_store:
#                        request.active_company = user_company
#                    else:
#                        # Security: If the session ID isn't in their managed list, kick them out
#                        if 'active_store_id' in request.session:
#                            del request.session['active_store_id']
#                        return redirect('select_store')
#                        
#                # Case C: Multiple stores assigned, nothing selected yet
#                elif store_count > 1:
#                    if 'admin' in request.path and request.path != reverse('select_store'):
#                        return redirect('select_store')
#        return self.get_response(request)




class StoreContextMiddleware:

#    Middleware to manage store context for multi-tenant access control.
#    Sets request.active_store and request.active_company based on user role.

    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Initialize context variables
        request.active_store = None
        request.active_company = None

        # Bypass for static files, logout, and store setter
        bypass_paths = ['/admin/logout/', '/static/', '/media/', '/set-store/']
        if any(request.path.startswith(p) for p in bypass_paths):
            return self.get_response(request)

        if request.user.is_authenticated:
            active_store_id = request.session.get('active_store_id')

            # SUPERUSER LOGIC
            if request.user.is_superuser:
                if active_store_id:
                    from .models import Store
                    store = Store.objects.select_related('company').filter(id=active_store_id).first()
                    if store:
                        request.active_store = store
                        request.active_company = store.company
                
                # Redirect to store selection if in admin without store
                if not request.active_store and 'admin' in request.path and request.path != reverse('select_store'):
                    return redirect('select_store')
                
                return self.get_response(request)

            # REGULAR USER LOGIC
            user_company = getattr(request.user, 'company', None)

            if user_company:
                allowed_stores = request.user.managed_stores.all()
                store_count = allowed_stores.count()

                # Single store - auto-select
                if store_count == 1:
                    request.active_store = allowed_stores.first()
                    request.active_company = user_company
                    request.session['active_store_id'] = request.active_store.id
                
                # Multiple stores with session selection
                elif active_store_id:
                    request.active_store = allowed_stores.filter(id=active_store_id).first()

                    if request.active_store:
                        request.active_company = user_company
                    else:
                        # Security: Clear invalid session
                        if 'active_store_id' in request.session:
                            del request.session['active_store_id']
                        return redirect('select_store')
                        
                # Multiple stores, nothing selected
                elif store_count > 1:
                    if 'admin' in request.path and request.path != reverse('select_store'):
                        return redirect('select_store')
                        
        return self.get_response(request)


class SecurityHeadersMiddleware:
#    Middleware to add security headers to all responses.
#    Implements defense-in-depth security measures.
    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        
        # Content Security Policy - restrictive but allows Django admin to work
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",  # Django admin needs inline scripts
            "style-src 'self' 'unsafe-inline'",   # Django admin needs inline styles
            "img-src 'self' data: blob:",
            "font-src 'self'",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "base-uri 'self'",
        ]
        response['Content-Security-Policy'] = "; ".join(csp_directives)
        
        # Additional security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        
        return response


class InputSanitizationMiddleware:
    """
    Flexible validator for ESL Tag IDs.
    Accepts alphanumeric serials (0-9, A-Z) between 8 and 15 characters.
    """

    @classmethod
    def sanitize_tag_id(cls, raw_id):
        """
        Removes special characters and spaces.
        Ensures length is within 8-15 characters.
        """
        if not raw_id:
            return None
        
        # Strip spaces and remove anything not 0-9 or A-Z
        cleaned = re.sub(r'[^0-9A-Za-z]', '', str(raw_id).strip())
        
        # Flexible length check: allowing 8 to 15 chars
        if 8 <= len(cleaned) <= 15:
            # We normalize to Upper for consistency in DB and MQTT topics
            return cleaned.upper()
        return None

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)