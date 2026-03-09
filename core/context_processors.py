from .models import Store
from .admin.base import admin_site

"""
DJANGO CONTEXT PROCESSORS: GLOBAL TEMPLATE VARIABLES
----------------------------------------------------
A Context Processor is a Python function that adds data to the 'Context'
of every single HTML template rendered in the project.

In SAIS, we use this to make the following variables available
EVERYWHERE (including navigation bars, sidebars, and footers):
- {{ active_store }}: The store currently being managed.
- {{ user_stores }}: The list of all stores the user is allowed to see.
- {{ dashboard_url }}: The link to the store analytics page.

Think of this as a 'Global Variable' for the Front-End.
"""

def store_context(request):
    """
    GLOBAL STORE DATA
    -----------------
    Injects the current store and the list of available stores into
    every HTML page. This is what powers the store-selector dropdown
    in the top header of the platform.
    """
    if not request.user.is_authenticated:
        # If not logged in, we don't need to provide store data
        return {}

    # 1. Identify which stores this user is allowed to access
    if request.user.is_superuser:
        user_stores = Store.objects.all()
    else:
        user_stores = request.user.managed_stores.all()

    # 2. Retrieve the currently 'Selected' store from the User's session
    active_store_id = request.session.get('active_store_id')
    active_store = None
    
    if active_store_id:
        active_store = user_stores.filter(id=active_store_id).first()
    
    # 3. AUTO-SELECT FALLBACK:
    # If no store is in the session, just pick the first one they own.
    if not active_store and user_stores.exists():
        active_store = user_stores.first()
        request.session['active_store_id'] = getattr(active_store, 'id', None)

    # 4. ADMIN BRANDING:
    # We also pull context from our custom AdminSite (SAISAdminSite) to get
    # things like the custom CSS/JS links we defined in base.py.
    context = admin_site.each_context(request)

    # MERGE: Add our store data to the context
    context.update({
        'user_stores': user_stores,
        'active_store': active_store
    })

    return context
