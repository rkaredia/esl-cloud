from django.shortcuts import redirect
from django.urls import reverse

class StoreContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Initialize our "Global Variables" as None
        request.active_store = None
        request.active_company = None

        # Bypass for static files, logout, and the setter view itself
        if any(request.path.startswith(p) for p in ['/admin/logout/', '/static/', '/set-store/']):
            return self.get_response(request)

        if request.user.is_authenticated:
            active_store_id = request.session.get('active_store_id')

            # --- 1. SUPERUSER LOGIC (The "God Mode" bypass) ---
            if request.user.is_superuser:
                if active_store_id:
                    from .models import Store
                    # Use select_related to get the company in one database query
                    store = Store.objects.select_related('company').filter(id=active_store_id).first()
                    if store:
                        request.active_store = store
                        request.active_company = store.company
                
                # If a superuser is in Admin but hasn't picked a store, send them to the list
                if not request.active_store and 'admin' in request.path and request.path != reverse('select_store'):
                    return redirect('select_store')
                
                return self.get_response(request)

            # --- 2. REGULAR USER LOGIC (Owners & Managers) ---
            user_company = getattr(request.user, 'company', None)
            
            if user_company:
                user_stores = user_company.stores.all() 
                store_count = user_stores.count()

                # Case A: Only 1 store -> Set it automatically
                if store_count == 1:
                    request.active_store = user_stores.first()
                    request.active_company = user_company
                    request.session['active_store_id'] = request.active_store.id
                
                # Case B: Multiple stores, one is already in the session
                elif active_store_id:
                    if request.user.role == 'owner':
                        # Owners can see any store in their company
                        request.active_store = user_company.stores.filter(id=active_store_id).first()
                    else:
                        # Managers are restricted to their assigned list
                        request.active_store = request.user.managed_stores.filter(id=active_store_id).first()

                    if request.active_store:
                        request.active_company = user_company
                    else:
                        # If the store in session is invalid for this user, clear it
                        del request.session['active_store_id']
                        return redirect('select_store')
                        
                # Case C: Multiple stores, nothing selected yet
                elif store_count > 1:
                    if 'admin' in request.path and request.path != reverse('select_store'):
                        return redirect('select_store')

        return self.get_response(request)