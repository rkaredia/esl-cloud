from .models import Store

def store_context(request):
    if not request.user.is_authenticated:
        return {}

    # 1. Get allowed stores
    if request.user.is_superuser:
        user_stores = Store.objects.all()
    else:
        user_stores = request.user.managed_stores.all()

    # 2. Get active store from session
    active_store_id = request.session.get('active_store_id')
    active_store = None
    
    if active_store_id:
        active_store = user_stores.filter(id=active_store_id).first()
    
    # 3. Fallback to first store if none selected
    if not active_store and user_stores.exists():
        active_store = user_stores.first()
        request.session['active_store_id'] = active_store.id

    return {
        'user_stores': user_stores,
        'active_store': active_store  # Matches your template variable
    }