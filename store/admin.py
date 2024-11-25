from django.contrib import admin

from .models import *

admin.site.register(Customer)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(ShippingAddress)

# For a basic admin interface:
#admin.site.register(Product)

# OR for a more customized admin interface:
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'brand', 'category', 'stock', 'is_available')
    list_filter = ('category', 'brand', 'is_available')
    search_fields = ('name', 'description')
    list_editable = ('price', 'stock', 'is_available')

# Add this new admin registration for Review
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('product', 'customer', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('product__name', 'customer__name', 'comment')
    readonly_fields = ('created_at',)