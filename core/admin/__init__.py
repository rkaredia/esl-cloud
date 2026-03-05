from .base import admin_site
from .organisation import CompanyAdmin, StoreAdmin, CustomUserAdmin
from .inventory import ProductAdmin, SupplierAdmin
from .hardware import GatewayAdmin, TagHardwareAdmin, ESLTagAdmin
from .monitoring import CustomGroupResultAdmin

__all__ = [
    'admin_site',
    'CompanyAdmin',
    'StoreAdmin',
    'CustomUserAdmin',
    'ProductAdmin',
    'SupplierAdmin',
    'GatewayAdmin',
    'TagHardwareAdmin',
    'ESLTagAdmin',
    'CustomGroupResultAdmin',
]
