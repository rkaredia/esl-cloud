from django.shortcuts import redirect
from django.urls import reverse
import re

class StoreContextMiddleware:
    """
    Middleware to manage store context for multi-tenant access control.
    Sets request.active_store and request.active_company based on user role and session.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Initialize context variables
        request.active_store = None
        request.active_company = None

        # Bypass for static files, logout, and store setter
        bypass_paths = ['/admin/logout/', '/static/', '/media/', '/set-store/', '/help/']
        if any(request.path.startswith(p) for p in bypass_paths):
            return self.get_response(request)

        if request.user.is_authenticated:
            active_store_id = request.session.get('active_store_id')

            # --- SUPERUSER LOGIC ---
            if request.user.is_superuser:
                if active_store_id:
                    from .models import Store
                    store = Store.objects.select_related('company').filter(id=active_store_id).first()
                    if store:
                        request.active_store = store
                        request.active_company = store.company
                
                # Redirect to store selection if in admin without an active store
                if not request.active_store and 'admin' in request.path and request.path != reverse('select_store'):
                    return redirect('select_store')
                
                return self.get_response(request)

            # --- REGULAR USER LOGIC (Owners, Managers, Staff) ---
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
                        # Security: Clear invalid session ID if it doesn't belong to the user's allowed stores
                        if 'active_store_id' in request.session:
                            del request.session['active_store_id']
                        return redirect('select_store')
                        
                # Multiple stores, nothing selected yet
                elif store_count > 1:
                    if 'admin' in request.path and request.path != reverse('select_store'):
                        return redirect('select_store')
                        
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """
    Middleware to add security headers to all responses.
    Implements defense-in-depth security measures.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        
        # Content Security Policy - restrictive but allows Django admin to work
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
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
    Validator and sanitizer for ESL Tag IDs.
    Accepts alphanumeric serials between 8 and 15 characters.
    """
    @classmethod
    def sanitize_tag_id(cls, raw_id):
        """
        Removes special characters and spaces, normalizing the ID to uppercase.
        """
        if not raw_id:
            return None
        cleaned = re.sub(r'[^0-9A-Za-z]', '', str(raw_id).strip())
        if 8 <= len(cleaned) <= 15:
            return cleaned.upper()
        return None

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)
