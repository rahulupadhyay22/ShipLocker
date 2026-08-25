import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse, Http404, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView
from django.views.generic.detail import SingleObjectMixin

from indiabox.mixins import LockerOwnershipMixin, SecureActionMixin
from indiabox.validators import validate_file_upload
from apps.payments.models import Payment
from apps.payments.services import RazorpayService

from .forms import (
    ProductLinkForm, ImageSearchForm,
    BoutiquePurchaseForm, LocalShopPurchaseForm, CustomRequestForm,
)
from .models import PersonalShopRequest, PersonalShopImage
from .utils import upload_personal_shop_image

security = logging.getLogger('security')

TYPE_FORM_MAP = {
    'product_link': (ProductLinkForm, 'personal_shop/request_form_product_link.html'),
    'image_search': (ImageSearchForm, 'personal_shop/request_form_image_search.html'),
    # Cart Screenshot was merged into Image Search — kept here so pre-existing requests
    # of this type (and their edit/detail links) keep working; no longer offered as its own card.
    'cart_screenshot': (ImageSearchForm, 'personal_shop/request_form_image_search.html'),
    'boutique_purchase': (BoutiquePurchaseForm, 'personal_shop/request_form_boutique.html'),
    'local_shop_purchase': (LocalShopPurchaseForm, 'personal_shop/request_form_local_shop.html'),
    'custom_request': (CustomRequestForm, 'personal_shop/request_form_custom.html'),
}

REQUEST_TYPE_CARDS = [
    {'type': 'product_link', 'label': 'Product Link', 'icon': 'link',
     'description': "Paste a link to something you found online and we'll buy it for you."},
    {'type': 'image_search', 'label': 'Image Search / Cart Screenshot', 'icon': 'image',
     'description': "Upload a product photo or cart screenshot and we'll source every item."},
    {'type': 'boutique_purchase', 'label': 'Boutique Purchase', 'icon': 'bag',
     'description': "Tell us the boutique and we'll buy or arrange alterations for you."},
    {'type': 'local_shop_purchase', 'label': 'Local Shop Purchase', 'icon': 'store',
     'description': "Name a local shop and we'll visit in person to buy the item. Hyderabad only."},
    {'type': 'custom_request', 'label': 'Custom Request', 'icon': 'lightbulb',
     'description': "Anything else — describe what you need and we'll take it from there."},
]

FORM_HOW_IT_WORKS = {
    'product_link': [
        ('You paste the link', 'Share the product URL from any Indian website.'),
        ('We verify & price', 'We confirm availability and the best price.'),
        ('You approve & pay', 'Review the quote and pay securely.'),
        ('We buy & deliver', 'We purchase and ship it to your CamelTrunk.'),
    ],
    'image_search': [
        ('You upload an image', 'Share a product photo or cart screenshot.'),
        ('We search & verify', "We'll find matches and confirm price."),
        ('You approve & pay', 'Review the quote and make secure payment.'),
        ('We buy & deliver', 'We purchase and ship to your Trunk.'),
    ],
    'boutique_purchase': [
        ('You share boutique details', 'Tell us what you want.'),
        ('We contact & confirm', 'We confirm availability & price.'),
        ('You approve & pay', 'Review and make payment.'),
        ('We buy / arrange alterations', 'We purchase or arrange as needed.'),
        ('Delivered to warehouse', 'We deliver to your CamelTrunk.'),
    ],
    'local_shop_purchase': [
        ('You share shop details', 'Tell us what and where.'),
        ('We visit & check', 'We check availability & quality.'),
        ('You approve & pay', 'Review the quote and pay.'),
        ('We buy the item', 'We purchase it for you.'),
        ('Delivered to warehouse', 'We deliver to your CamelTrunk.'),
    ],
    'custom_request': [
        ('You describe your need', 'Share your requirement.'),
        ('We analyze & research', 'Our team finds the best options.'),
        ('You get a quote', 'We share options and pricing.'),
        ('You approve & pay', 'Review and pay securely.'),
        ('We handle the rest', 'We purchase and deliver.'),
    ],
}

SUPPORTED_SITES = [
    {'name': 'Amazon.in', 'color': '#FF9900'},
    {'name': 'Flipkart', 'color': '#2874F0'},
    {'name': 'Myntra', 'color': '#FF3F6C'},
    {'name': 'Ajio', 'color': '#2B2B2B'},
    {'name': 'Meesho', 'color': '#9F2089'},
    {'name': 'FirstCry', 'color': '#F7941D'},
    {'name': 'Tata CLiQ', 'color': '#E21937'},
]
SUPPORTED_SITES_TYPES = {'product_link', 'image_search'}

SIDEBAR_TIPS = {
    'boutique_purchase': {
        'title': 'Tips for a Faster Quote',
        'items': [
            "Share the boutique's Instagram or website if it's online-only",
            'Mention size or measurements clearly',
            'Attach a reference photo if you have one',
        ],
    },
    'local_shop_purchase': {
        'title': 'Tips for a Faster Visit',
        'items': [
            "Share the shop's area or a nearby landmark",
            "Mention the shop's open hours if known",
            'Add a contact number so we can confirm details',
        ],
    },
    'custom_request': {
        'title': 'Good to Know',
        'items': [
            'Be as specific as possible about what you need',
            'Mention any brand or quality preferences',
            'Our team may follow up for more details',
        ],
    },
}

LIST_FILTER_PILLS = [
    ('submitted', 'Waiting Quote'),
    ('quotation_ready', 'Quote Ready'),
    ('paid', 'Paid'),
    ('purchased', 'Purchased'),
    ('delivered_to_warehouse', 'Delivered'),
]

EXPECTED_TURNAROUND = {
    'product_link': 'Usually within 24 hours',
    'image_search': 'Usually within 24 hours',
    'boutique_purchase': 'Usually within 24 hours',
    'local_shop_purchase': '3-5 business days — requires an in-person visit',
    'custom_request': 'Usually within 24 hours',
}

TIMELINE_STEPS = [
    ('created_at', 'Request Submitted'),
    ('executive_assigned_at', 'Executive Assigned'),
    ('searching_started_at', 'Searching for Product'),
    ('quotation_ready_at', 'Quotation Ready'),
    ('paid_at', 'Paid'),
    ('purchased_at', 'Purchased'),
    ('delivered_at', 'Delivered to Warehouse'),
    ('added_to_trunk_at', 'Added to My Trunk'),
]

# Spec 10's research-fee/expense-advance flows cycle status back through these
# after an initial payment (a second quotation needs its own payment). Every
# other timestamp is "set once, stays true forever" (matches the linear spec-07
# flow this timeline was built for), but paid_at must not still read as "done"
# while the request is sitting in one of these — that would tell the customer
# they're all paid up when a fresh payment is actually pending.
AWAITING_NEW_QUOTATION_PAYMENT_STATUSES = {
    'quotation_ready', 'quotation_declined', 'quotation_expired', 'payment_pending',
}


def build_timeline(obj):
    timeline = []
    for field, label in TIMELINE_STEPS:
        timestamp = getattr(obj, field)
        done = timestamp is not None
        if field == 'paid_at' and obj.status in AWAITING_NEW_QUOTATION_PAYMENT_STATUSES:
            done = False
            timestamp = None
        timeline.append({'label': label, 'timestamp': timestamp, 'done': done})
    if obj.status == 'cancelled':
        timeline.append({'label': 'Cancelled', 'timestamp': obj.cancelled_at, 'done': True})
    return timeline


class TrunkAssistDashboardView(LoginRequiredMixin, View):
    def get(self, request):
        recent = list(
            PersonalShopRequest.objects.filter(locker=request.user.locker)
            .select_related('active_quotation')
            .order_by('-created_at')[:5]
        )
        return render(request, 'personal_shop/dashboard.html', {
            'type_cards': REQUEST_TYPE_CARDS,
            'recent_requests': recent,
            'is_premium': request.user.locker.is_premium,
        })


class PersonalShopRequestListView(LoginRequiredMixin, LockerOwnershipMixin, ListView):
    model = PersonalShopRequest
    template_name = 'personal_shop/request_list.html'
    context_object_name = 'requests'
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset().select_related('active_quotation')
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        request_type = self.request.GET.get('request_type')
        if request_type:
            qs = qs.filter(request_type=request_type)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts_by_status = dict(
            PersonalShopRequest.objects.filter(locker=self.request.user.locker)
            .values_list('status')
            .annotate(n=Count('id'))
            .values_list('status', 'n')
        )
        context['status_choices'] = PersonalShopRequest.STATUS_CHOICES
        context['type_choices'] = PersonalShopRequest.REQUEST_TYPE_CHOICES
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_type'] = self.request.GET.get('request_type', '')
        context['status_pills'] = [
            {'value': '', 'label': 'All', 'count': sum(counts_by_status.values())},
            *[
                {'value': value, 'label': label, 'count': counts_by_status.get(value, 0)}
                for value, label in LIST_FILTER_PILLS
            ],
        ]
        return context


class PersonalShopRequestCreateView(LoginRequiredMixin, View):
    def _extra_context(self, request_type):
        return {
            'how_it_works': FORM_HOW_IT_WORKS.get(request_type, []),
            'supported_sites': SUPPORTED_SITES if request_type in SUPPORTED_SITES_TYPES else None,
            'sidebar_tips': SIDEBAR_TIPS.get(request_type),
        }

    def get(self, request, request_type):
        entry = TYPE_FORM_MAP.get(request_type)
        if entry is None:
            raise Http404('Unknown request type')
        FormClass, template = entry
        return render(request, template, {
            'form': FormClass(locker=request.user.locker), 'request_type': request_type,
            **self._extra_context(request_type),
        })

    def post(self, request, request_type):
        entry = TYPE_FORM_MAP.get(request_type)
        if entry is None:
            raise Http404('Unknown request type')
        FormClass, template = entry
        form = FormClass(request.POST, request.FILES, locker=request.user.locker)
        if not form.is_valid():
            return render(request, template, {
                'form': form, 'request_type': request_type, **self._extra_context(request_type),
            })

        reference_image = form.cleaned_data.get('reference_image') if 'reference_image' in form.fields else None
        if reference_image:
            try:
                validate_file_upload(reference_image)
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, template, {'form': form, 'request_type': request_type})

        with transaction.atomic():
            instance = form.save(commit=False)
            instance.locker = request.user.locker
            instance.request_type = request_type
            instance.status = 'reviewing' if request_type == 'custom_request' else 'submitted'
            instance.save()

            if reference_image:
                image_path = upload_personal_shop_image(
                    reference_image, request.user.locker.locker_id, instance.display_id
                )
                PersonalShopImage.objects.create(request=instance, image_path=image_path)

        security.info(
            f"Secure action: PersonalShopRequestCreateView by {request.user.email} "
            f"type={request_type} id={instance.display_id}"
        )
        return redirect('personal_shop:request_detail', pk=instance.pk)


class PersonalShopRequestDetailView(LoginRequiredMixin, LockerOwnershipMixin, DetailView):
    model = PersonalShopRequest
    template_name = 'personal_shop/request_detail.html'
    context_object_name = 'shop_request'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.object
        context['timeline'] = build_timeline(obj)
        context['images'] = obj.images.all()
        context['notes'] = obj.notes.all().order_by('-created_at')
        context['turnaround'] = EXPECTED_TURNAROUND.get(obj.request_type, '')
        context['payment'] = obj.payments.filter(status='captured').order_by('-paid_at').first()
        context['invoice'] = getattr(obj.active_quotation, 'invoice', None)
        return context


class PersonalShopRequestEditView(LoginRequiredMixin, LockerOwnershipMixin, SecureActionMixin, SingleObjectMixin, View):
    model = PersonalShopRequest

    def get(self, request, *args, **kwargs):
        obj = self.get_object()
        if not obj.is_editable:
            raise PermissionDenied('This request can no longer be edited.')
        FormClass = TYPE_FORM_MAP[obj.request_type][0]
        return render(request, 'personal_shop/request_edit.html', {
            'form': FormClass(instance=obj, locker=request.user.locker), 'shop_request': obj,
        })

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if not obj.is_editable:
            raise PermissionDenied('This request can no longer be edited.')
        FormClass = TYPE_FORM_MAP[obj.request_type][0]
        form = FormClass(request.POST, request.FILES, instance=obj, locker=request.user.locker)
        if not form.is_valid():
            return render(request, 'personal_shop/request_edit.html', {
                'form': form, 'shop_request': obj,
            })

        reference_image = form.cleaned_data.get('reference_image') if 'reference_image' in form.fields else None
        if reference_image:
            try:
                validate_file_upload(reference_image)
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, 'personal_shop/request_edit.html', {
                    'form': form, 'shop_request': obj,
                })

        form.save()

        if reference_image:
            image_path = upload_personal_shop_image(
                reference_image, request.user.locker.locker_id, obj.display_id
            )
            PersonalShopImage.objects.create(request=obj, image_path=image_path)

        return redirect('personal_shop:request_detail', pk=obj.pk)


class PersonalShopRequestCancelView(LoginRequiredMixin, LockerOwnershipMixin, SecureActionMixin, SingleObjectMixin, View):
    model = PersonalShopRequest

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if not obj.is_cancellable:
            return HttpResponseBadRequest('This request has already been purchased and cannot be cancelled.')

        if obj.status == 'paid':
            obj.refund_required = True
        obj.status = 'cancelled'
        obj.cancelled_at = timezone.now()
        obj.save()
        messages.success(request, 'Request cancelled.')
        return redirect('personal_shop:request_detail', pk=obj.pk)


class PersonalShopQuotationView(LoginRequiredMixin, LockerOwnershipMixin, SingleObjectMixin, View):
    model = PersonalShopRequest

    def get(self, request, *args, **kwargs):
        obj = self.get_object()
        quotation = obj.active_quotation
        if quotation is None:
            raise Http404('No quotation for this request')

        if quotation.is_expired:
            quotation.status = 'expired'
            quotation.save()
            obj.status = 'quotation_expired'
            obj.save()

        quotation.refresh_service_fee_discount()

        remaining = quotation.valid_until - timezone.now()
        if remaining.total_seconds() > 0:
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            expires_in = f"{hours}h {minutes}m"
        else:
            expires_in = 'Expired'

        return render(request, 'personal_shop/quotation.html', {
            'shop_request': obj,
            'quotation': quotation,
            'line_items': quotation.line_items.all(),
            'expires_in': expires_in,
            'invoice': getattr(quotation, 'invoice', None),
            'premium_savings': obj.locker.premium_savings_display,
        })


class PersonalShopQuotationDeclineView(LoginRequiredMixin, LockerOwnershipMixin, SecureActionMixin, SingleObjectMixin, View):
    model = PersonalShopRequest

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        quotation = obj.active_quotation
        if quotation is None or quotation.status != 'pending':
            messages.error(request, 'No pending quotation to decline.')
            return redirect('personal_shop:request_detail', pk=obj.pk)

        quotation.status = 'declined'
        quotation.save()
        obj.status = 'quotation_declined'
        obj.save()
        messages.success(request, 'Quotation declined.')
        return redirect('personal_shop:request_detail', pk=obj.pk)


class CreatePersonalShopPaymentOrderView(LoginRequiredMixin, LockerOwnershipMixin, SecureActionMixin, SingleObjectMixin, View):
    model = PersonalShopRequest

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        quotation = obj.active_quotation
        if quotation is None:
            return JsonResponse({'error': 'No active quotation'}, status=404)

        if quotation.status != 'pending':
            return JsonResponse({'error': 'No pending quotation to pay'}, status=400)

        if quotation.is_expired:
            quotation.status = 'expired'
            quotation.save()
            obj.status = 'quotation_expired'
            obj.save()
            return JsonResponse({'error': 'Quotation expired'}, status=400)

        quotation.refresh_service_fee_discount()

        service = RazorpayService()
        if not service.is_enabled:
            return JsonResponse({'error': 'Payments not configured'}, status=503)

        amount = quotation.total_amount
        amount_paise = int(amount * 100)

        with transaction.atomic():
            existing = Payment.objects.select_for_update().filter(
                personal_shop_request=obj,
                user=request.user,
                status='pending',
                created_at__gte=timezone.now() - timedelta(minutes=30),
            ).order_by('-created_at').first()

            if existing and existing.razorpay_order_id:
                return JsonResponse({
                    'order_id': existing.razorpay_order_id,
                    'amount': int(existing.amount * 100),
                    'currency': existing.currency,
                    'key_id': service.key_id,
                    'payment_pk': str(existing.pk),
                })

            payment = Payment.objects.create(
                user=request.user,
                personal_shop_request=obj,
                amount=amount,
                currency='INR',
                payment_method='razorpay',
                status='pending',
                description=f'TrunkAssist quotation for {obj.display_id}',
            )

            order = service.create_order(
                amount_paise=amount_paise,
                currency='INR',
                receipt=payment.display_id,
                notes={'personal_shop_request_id': str(obj.pk)},
            )

            if not order:
                payment.status = 'failed'
                payment.failure_reason = 'Order creation failed'
                payment.save()
                return JsonResponse({'error': 'Payment order creation failed'}, status=502)

            payment.razorpay_order_id = order['id']
            payment.save()

            obj.status = 'payment_pending'
            obj.save()

        security.info(
            f"Secure action: CreatePersonalShopPaymentOrderView by {request.user.email} "
            f"request={obj.display_id} amount={amount} premium_discount={quotation.premium_discount_amount}"
        )

        return JsonResponse({
            'order_id': order['id'],
            'amount': amount_paise,
            'currency': 'INR',
            'key_id': service.key_id,
            'payment_pk': str(payment.pk),
        })


class PersonalShopPaymentConfirmationView(LoginRequiredMixin, LockerOwnershipMixin, SingleObjectMixin, View):
    model = PersonalShopRequest

    def get(self, request, *args, **kwargs):
        obj = self.get_object()
        payment = obj.payments.filter(status='captured').order_by('-paid_at').first()
        return render(request, 'personal_shop/payment_confirmation.html', {
            'shop_request': obj,
            'payment': payment,
        })
