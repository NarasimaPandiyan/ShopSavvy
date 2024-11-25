from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
import json
import datetime
from django.db.models import Q
from .models import * 
from .utils import cookieCart, cartData, guestOrder
from django.contrib.auth import login, authenticate
from .forms import CustomUserCreationForm, CustomerForm, UserProfileForm, UserUpdateForm
import stripe
from django.conf import settings
from django.urls import reverse
import logging
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Count

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add these settings at the top of the file
stripe.api_key = settings.STRIPE_SECRET_KEY

def store(request):
	data = cartData(request)
	cartItems = data['cartItems']
	
	# Start with all products
	products = Product.objects.filter(is_available=True)
	
	# Get all filter parameters
	filters = {}
	category_slug = request.GET.get('category')
	brand = request.GET.get('brand')
	min_price = request.GET.get('min_price')
	max_price = request.GET.get('max_price')
	sort_by = request.GET.get('sort')
	
	# Apply category filter
	if category_slug:
		filters['category'] = category_slug
		try:
			category = Category.objects.get(slug=category_slug)
			if category.is_department:
				subcategories = Category.objects.filter(parent=category)
				products = products.filter(category__in=subcategories)
			else:
				products = products.filter(category=category)
		except Category.DoesNotExist:
			pass
	
	# Apply brand filter
	if brand:
		filters['brand'] = brand
		products = products.filter(brand=brand)
	
	# Apply price filters
	if min_price:
		try:
			filters['min_price'] = min_price
			products = products.filter(price__gte=float(min_price))
		except ValueError:
			pass
	
	if max_price:
		try:
			filters['max_price'] = max_price
			products = products.filter(price__lte=float(max_price))
		except ValueError:
			pass
	
	# Apply sorting
	if sort_by:
		filters['sort'] = sort_by
		if sort_by == 'price_asc':
			products = products.order_by('price')
		elif sort_by == 'price_desc':
			products = products.order_by('-price')
		elif sort_by == 'rating_desc':
			products = sorted(list(products), key=lambda p: p.average_rating or 0, reverse=True)
		elif sort_by == 'rating_asc':
			products = sorted(list(products), key=lambda p: p.average_rating or 0)
	
	# Get all categories for the sidebar
	main_categories = Category.objects.filter(is_department=True)
	subcategories = Category.objects.filter(is_department=False)
	
	# Get all unique brands
	brands = Product.objects.values_list('brand', flat=True).distinct().order_by('brand')
	
	# Debug print categories
	print("\nMain Categories:")
	for cat in main_categories:
		print(f"- {cat.name} (slug: {cat.slug})")
	
	print("\nSubcategories:")
	for cat in subcategories:
		print(f"- {cat.name} (slug: {cat.slug}, parent: {cat.parent})")
	
	# Add to the context dictionary
	sort_options = [
		{'value': 'price_asc', 'label': 'Price: Low to High'},
		{'value': 'price_desc', 'label': 'Price: High to Low'},
		{'value': 'name_asc', 'label': 'Name: A to Z'},
		{'value': 'name_desc', 'label': 'Name: Z to A'},
	]
	
	context = {
		'products': products,
		'cartItems': cartItems,
		'main_categories': main_categories,
		'subcategories': subcategories,
		'brands': brands,
		'active_filters': filters,
		'current_category': category_slug,
		'current_brand': brand,
		'sort_options': sort_options,
	}
	return render(request, 'store/store.html', context)


def cart(request):
	data = cartData(request)

	cartItems = data['cartItems']
	order = data['order']
	items = data['items']

	context = {'items':items, 'order':order, 'cartItems':cartItems}
	return render(request, 'store/cart.html', context)

def checkout(request):
    data = cartData(request)
    cartItems = data['cartItems']
    order = data['order']
    items = data['items']

    # Check if cart is empty (handle both Order object and dictionary)
    if isinstance(order, dict):
        cart_total = order.get('cart_total', 0)
    else:
        cart_total = order.get_cart_total

    if cart_total == 0:
        messages.warning(request, 'Your cart is empty')
        return redirect('cart')

    context = {
        'items': items,
        'order': order,
        'cartItems': cartItems,
        'shipping': True,
        'cart_total': cart_total,
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
    }
    return render(request, 'store/checkout.html', context)

def updateItem(request):
	data = json.loads(request.body)
	productId = data['productId']
	action = data['action']
	print('Action:', action)
	print('Product:', productId)

	customer = request.user.customer
	product = Product.objects.get(id=productId)
	order, created = Order.objects.get_or_create(customer=customer, complete=False)

	orderItem, created = OrderItem.objects.get_or_create(order=order, product=product)

	if action == 'add':
		orderItem.quantity = (orderItem.quantity + 1)
	elif action == 'remove':
		orderItem.quantity = (orderItem.quantity - 1)

	orderItem.save()

	if orderItem.quantity <= 0:
		orderItem.delete()

	return JsonResponse('Item was added', safe=False)

@csrf_exempt
def process_order(request):
    try:
        data = json.loads(request.body)
        
        with transaction.atomic():
            if request.user.is_authenticated:
                customer = request.user.customer
                order, created = Order.objects.get_or_create(customer=customer, complete=False)
            else:
                # Handle guest user
                order = Order.objects.create(complete=False)
                
                # Create guest customer
                customer = Customer.objects.create(
                    name=data['shipping']['name'],
                    email=data['shipping']['email']
                )
            
            # Process items and update stock
            items = order.orderitem_set.all()
            for item in items:
                if item.product.stock >= item.quantity:
                    item.product.stock -= item.quantity
                    item.product.save()
                else:
                    raise Exception(f'Insufficient stock for {item.product.name}')
            
            # Create shipping address
            if data.get('shipping'):
                ShippingAddress.objects.create(
                    customer=customer,
                    order=order,
                    address=data['shipping']['address'],
                    city=data['shipping']['city'],
                    state=data['shipping']['state'],
                    zipcode=data['shipping']['zipcode'],
                )
            
            # Complete the order
            order.complete = True
            order.transaction_id = data.get('paymentIntentId', str(datetime.datetime.now().timestamp()))
            order.save()
            
            return JsonResponse({
                'status': 'success',
                'order_id': order.id
            })
            
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    except KeyError as e:
        return JsonResponse({'status': 'error', 'message': f'Missing field: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

def product_detail(request, product_id):
	data = cartData(request)
	cartItems = data['cartItems']
	
	product = get_object_or_404(Product, id=product_id)
	
	# Get related products from the same category
	related_products = Product.objects.filter(
		category=product.category
	).exclude(id=product.id)[:4]
	
	context = {
		'product': product,
		'cartItems': cartItems,
		'related_products': related_products,
	}
	return render(request, 'store/product_detail.html', context)

def register_user(request):
	if request.method == 'POST':
		form = CustomUserCreationForm(request.POST)
		if form.is_valid():
			user = form.save()
			# Create customer profile
			Customer.objects.create(
				user=user,
				name=user.username,
				email=user.email
			)
			login(request, user)
			return redirect('checkout')
	else:
		form = CustomUserCreationForm()
	return render(request, 'store/register.html', {'form': form})

def login_view(request):
	if request.method == 'POST':
		username = request.POST.get('username')
		password = request.POST.get('password')
		user = authenticate(request, username=username, password=password)
		if user is not None:
			login(request, user)
			return redirect('checkout')
	return render(request, 'store/login.html')

@csrf_exempt
def create_payment_intent(request):
    try:
        data = json.loads(request.body)
        amount = float(data['amount'])
        
        # Create payment intent
        intent = stripe.PaymentIntent.create(
            amount=int(amount * 100),  # Convert to cents
            currency='usd'
        )
        
        return JsonResponse({
            'clientSecret': intent.client_secret
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

def login_register_choice(request):
	"""View to let users choose between login, register, or guest checkout"""
	# If user is already authenticated, redirect to checkout
	if request.user.is_authenticated:
		return redirect('checkout')
		
	return render(request, 'store/login_register_choice.html')

def payment_success(request):
    # Get the most recent completed order for the user
    if request.user.is_authenticated:
        order = Order.objects.filter(
            customer=request.user.customer, 
            complete=True
        ).order_by('-date_ordered').first()
    else:
        # For guest users, get order from session
        order_id = request.session.get('last_order_id')
        order = Order.objects.filter(id=order_id).first() if order_id else None

    context = {
        'order': order,
        'items': order.orderitem_set.all() if order else None,
        'total': order.get_cart_total if order else 0,
    }
    return render(request, 'store/payment_success.html', context)

def payment_failed(request):
	error_message = request.GET.get('error', 'An error occurred during payment processing.')
	return render(request, 'store/payment_failed.html', {'error_message': error_message})

@login_required
def profile(request):
    customer = request.user.customer
    orders = Order.objects.filter(customer=customer, complete=True).order_by('-date_ordered')
    
    context = {
        'customer': customer,
        'orders': orders,
    }
    return render(request, 'store/profile.html', context)


def logout_view(request):
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, 'You have been successfully logged out.')
    return redirect('store')

def orderSuccess(request, order_id):
    """
    Display the order success page with order details
    """
    try:
        # Get the order
        if request.user.is_authenticated:
            order = get_object_or_404(Order, id=order_id, customer=request.user.customer)
        else:
            order = get_object_or_404(Order, id=order_id)

        # Ensure order is complete
        if not order.complete:
            messages.error(request, 'Invalid order')
            return redirect('store')

        context = {
            'order': order,
            'items': order.orderitem_set.all(),
            'shipping': order.shipping,
            'total': order.get_cart_total,
            'shipping_address': order.shippingaddress_set.first(),
        }
        
        return render(request, 'store/order_success.html', context)
        
    except Order.DoesNotExist:
        messages.error(request, 'Order not found')
        return redirect('store')

@login_required
def add_review(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id)
        rating = int(request.POST.get('rating'))
        comment = request.POST.get('comment')
        
        # Check if user already reviewed this product
        existing_review = Review.objects.filter(
            product=product,
            customer=request.user.customer
        ).first()
        
        if existing_review:
            existing_review.rating = rating
            existing_review.comment = comment
            existing_review.save()
            messages.success(request, 'Your review has been updated!')
        else:
            Review.objects.create(
                product=product,
                customer=request.user.customer,
                rating=rating,
                comment=comment
            )
            messages.success(request, 'Thank you for your review!')
            
    return redirect('product_detail', product_id=product_id)
