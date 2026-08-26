document.addEventListener('DOMContentLoaded', function () {
    var feeField = document.getElementById('id_service_fee_standard_amount');
    var researchFeeField = document.getElementById('id_research_fee_amount');
    var totalField = document.getElementById('id_total_amount');
    var subtotalField = document.getElementById('id_subtotal');

    // django-unfold's autocomplete_fields widget is select2, a jQuery plugin —
    // it notifies listeners through jQuery's event system, which a native
    // element.addEventListener('change', ...) doesn't see. Bind through
    // django.jQuery (the same noConflict-namespaced jQuery instance Django
    // admin and unfold's own select2 init script use) so this catches both
    // native and jQuery-triggered 'change' events either way — and lets us
    // delegate on dynamically added inline formset rows (see below).
    var $ = window.django && window.django.jQuery;
    if (!$) return;

    // Tracks whether service_fee_amount's current value is still "ours" (an
    // auto-suggestion we can keep recalculating as line items change) or
    // something staff typed by hand, which must not be silently overwritten.
    // Any genuine keypress flips it false; our own programmatic field.value =
    // ... assignments never fire 'input', so writing the suggestion back
    // never re-triggers this itself.
    var feeAutoFilled = true;
    if (feeField) {
        feeField.addEventListener('input', function () { feeAutoFilled = false; });
    }

    // Whether the discount applies at all (isPremiumLocker), and the discount
    // rate itself, are both learned from the suggested-fee endpoint's
    // fee/premium_preview pair — never a second hardcoded copy of
    // Locker.PREMIUM_SERVICE_FEE_DISCOUNT_RATE. premium_preview is only
    // non-null for a Premium locker's request.
    var isPremiumLocker = false;
    var serviceFeeDiscountRate = 0;
    function notePremiumStatus(data) {
        isPremiumLocker = data.premium_preview !== null && data.premium_preview !== undefined;
        if (isPremiumLocker && data.fee !== null && data.fee !== undefined) {
            serviceFeeDiscountRate = 1 - (parseFloat(data.premium_preview) / parseFloat(data.fee));
        }
    }

    function suggestedFeeBaseUrl() {
        // Strips a trailing "add/" or "<uuid>/change/" segment off the current
        // page path to recover this ModelAdmin's base URL, so the endpoint is
        // never hardcoded — works under any admin mount path (ADMIN_URL, etc.).
        var path = window.location.pathname;
        return path.replace(/(?:add|[0-9a-f-]{36}\/change)\/?$/, '');
    }

    function fieldValue(id) {
        var el = document.getElementById(id);
        var value = el ? parseFloat(el.value) : 0;
        return isNaN(value) ? 0 : value;
    }

    function currentQuotationType() {
        var quotationTypeField = document.getElementById('id_quotation_type');
        // 'purchase' matches the field's own model default, so a fresh add
        // form with nothing touched yet still resolves to the right type.
        return quotationTypeField ? quotationTypeField.value : 'purchase';
    }

    // Mirrors PersonalShopQuotation.clean()'s QUOTATION_TYPE_ALLOWED_REQUEST_TYPES
    // check (research_fee only for custom_request, expense_advance only for
    // boutique_purchase/local_shop_purchase) — the server is still the actual
    // enforcement, this just stops staff from picking an invalid combo in the
    // first place instead of only rejecting it after they hit Save.
    function updateQuotationTypeOptions(allowedTypes, allowReset) {
        var quotationTypeField = document.getElementById('id_quotation_type');
        if (!quotationTypeField) return false;
        quotationTypeField.querySelectorAll('option').forEach(function (opt) {
            var isAllowed = allowedTypes.indexOf(opt.value) !== -1;
            opt.hidden = !isAllowed;
            opt.disabled = !isAllowed;
        });
        if (allowReset && allowedTypes.indexOf(quotationTypeField.value) === -1) {
            // Current selection just became invalid for this request (e.g. was
            // 'research_fee', request changed to a product_link) — fall back to
            // 'purchase' (always allowed) and let the existing quotation_type
            // change handler apply the fee-fill/clear/visibility side effects,
            // rather than duplicating that logic here. allowReset is false on
            // initial page load for an edit form — an already-saved quotation's
            // value was valid when saved, there's nothing to correct, only
            // options to filter so the *next* pick is constrained too.
            quotationTypeField.value = 'purchase';
            quotationTypeField.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        return false;
    }

    // Finds the field's whole row/group (label + input + help text), not just
    // the input, so hiding it doesn't leave an orphaned label behind. Climbs
    // from the input until it also contains the associated <label for="...">
    // — that boundary holds regardless of the admin theme's exact markup,
    // unlike hardcoding a specific wrapper class name.
    function fieldRow(inputId) {
        var input = document.getElementById(inputId);
        if (!input) return null;
        var label = document.querySelector('label[for="' + inputId + '"]');
        if (!label) return input.parentElement;
        var node = input.parentElement;
        while (node && !node.contains(label)) {
            node = node.parentElement;
        }
        return node || input.parentElement;
    }

    // Which fields are relevant per quotation_type — mirrors the total_amount
    // formula exactly: a field hidden here is a field that formula ignores.
    var FIELDS_BY_TYPE = {
        purchase: ['id_domestic_shipping_amount', 'id_service_fee_standard_amount', 'id_payment_gateway_charge', 'id_subtotal'],
        research_fee: ['id_research_fee_amount'],
        expense_advance: ['id_travel_expense_amount'],
    };
    var ALL_TOGGLABLE_FIELD_IDS = [
        'id_domestic_shipping_amount', 'id_service_fee_standard_amount', 'id_research_fee_amount',
        'id_travel_expense_amount', 'id_payment_gateway_charge', 'id_subtotal',
    ];

    function updateFieldVisibility() {
        var type = currentQuotationType();
        var visible = FIELDS_BY_TYPE[type] || ALL_TOGGLABLE_FIELD_IDS;
        ALL_TOGGLABLE_FIELD_IDS.forEach(function (id) {
            var row = fieldRow(id);
            if (row) row.style.display = visible.indexOf(id) === -1 ? 'none' : '';
        });
        // Line items only feed the Purchase total — hide the whole inline
        // section for Research Fee/Expense Advance. django-unfold wraps a
        // TabularInline in <div id="<fk related_name>-group">, not a
        // ".inline-group" class (that was a wrong guess — this line never
        // matched anything, so the section stayed visible for every type).
        // PersonalShopQuotationLineItem's FK to PersonalShopQuotation declares
        // related_name='line_items', so the rendered id is "line_items-group".
        var lineItemsGroup = document.getElementById('line_items-group');
        if (lineItemsGroup) {
            lineItemsGroup.style.display = type === 'purchase' ? '' : 'none';
        }
    }

    // The Quotation Line Items inline is rendered right there on the add/change
    // form (not just after save) — sum qty*unit_amount across every row that
    // isn't marked for deletion. Selector is name-suffix based ($=), not tied
    // to the formset's prefix, so it doesn't matter what Django names it, and
    // it naturally picks up rows added via "Add another Quotation Line Item"
    // without any extra wiring.
    function computeLineItemsSubtotal() {
        var total = 0;
        document.querySelectorAll('input[name$="-qty"]').forEach(function (qtyInput) {
            var rowPrefix = qtyInput.name.slice(0, -'-qty'.length);
            var deleteInput = document.querySelector('input[name="' + rowPrefix + '-DELETE"]');
            if (deleteInput && deleteInput.checked) return;
            var unitInput = document.querySelector('input[name="' + rowPrefix + '-unit_amount"]');
            var qty = parseFloat(qtyInput.value) || 0;
            var unit = unitInput ? (parseFloat(unitInput.value) || 0) : 0;
            total += qty * unit;
        });
        return total;
    }

    // Live Total Amount preview — mirrors PersonalShopQuotationAdmin.save_related's
    // per-quotation_type formula exactly: research_fee/expense_advance are
    // standalone upfront fees, nothing else factored in; purchase adds the
    // line-items subtotal on top of shipping/service-fee/gateway-charge.
    var AMOUNT_FIELD_IDS = [
        'id_domestic_shipping_amount', 'id_service_fee_standard_amount',
        'id_research_fee_amount', 'id_travel_expense_amount', 'id_payment_gateway_charge',
    ];

    function recalcTotal() {
        var subtotal = computeLineItemsSubtotal();
        if (subtotalField) subtotalField.value = subtotal.toFixed(2);

        if (!totalField) return;
        var type = currentQuotationType();
        var total;
        if (type === 'research_fee') {
            total = fieldValue('id_research_fee_amount');
        } else if (type === 'expense_advance') {
            total = fieldValue('id_travel_expense_amount');
        } else {
            var serviceFee = fieldValue('id_service_fee_standard_amount');
            if (isPremiumLocker) {
                serviceFee = Math.round(serviceFee * (1 - serviceFeeDiscountRate) * 100) / 100;
            }
            total = subtotal
                + fieldValue('id_domestic_shipping_amount')
                + serviceFee
                + fieldValue('id_payment_gateway_charge');
        }
        totalField.value = total.toFixed(2);
    }

    function clearField(field) {
        if (!field) return;
        field.value = '';
        if (field === feeField) feeAutoFilled = true; // cleared -> fair game to auto-fill again
        recalcTotal();
    }

    // pricing.suggested_service_fee (server-side) returns whichever is higher
    // of (product_value * rate) or the flat minimum for product_link/
    // image_search/cart_screenshot/boutique_purchase, and a flat amount for
    // local_shop_purchase/custom_request regardless of product_value — the
    // server decides which, this just passes the current line-items subtotal
    // through as the candidate "product value" whenever there's one to give.
    function refreshFeeSuggestion(field, requestId, productValue) {
        if (!field || !requestId) return;
        var url = suggestedFeeBaseUrl() + 'suggested-fee/' + requestId + '/';
        if (productValue !== undefined && productValue !== null) {
            url += '?product_value=' + productValue;
        }
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                notePremiumStatus(data);
                if (data.fee === null) { recalcTotal(); return; }
                field.value = data.fee;
                if (field === feeField) feeAutoFilled = true;
                recalcTotal();
            });
    }

    // Research Fee amount has no percentage formula (flat, ignores product
    // value) and research_fee quotations don't use the line-items inline at
    // all (see updateFieldVisibility) — a one-shot "fill if still empty"
    // suggestion is enough, no live recompute needed.
    function fetchFlatSuggestionIfEmpty(field, requestId) {
        if (!field || !requestId) return;
        var isEmpty = !field.value || parseFloat(field.value) === 0;
        if (!isEmpty) return;
        refreshFeeSuggestion(field, requestId, null);
    }

    var feeSuggestionDebounce = null;
    function scheduleFeeSuggestionRefresh() {
        // Only the Purchase service fee recalculates as line items change —
        // and only while staff haven't overridden it by hand (feeAutoFilled).
        if (currentQuotationType() !== 'purchase' || !feeAutoFilled) return;
        var requestField = document.getElementById('id_request');
        var requestId = requestField ? requestField.value : null;
        if (!requestId) return;
        clearTimeout(feeSuggestionDebounce);
        feeSuggestionDebounce = setTimeout(function () {
            refreshFeeSuggestion(feeField, requestId, computeLineItemsSubtotal());
        }, 400);
    }

    AMOUNT_FIELD_IDS.forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('input', recalcTotal);
    });
    $(document).on('change', '#id_quotation_type', recalcTotal);
    $(document).on('change', '#id_quotation_type', updateFieldVisibility);
    // Delegated (not bound to specific elements) so rows added later via
    // "Add another Quotation Line Item", or removed via its delete button,
    // are picked up without re-binding anything.
    $(document).on('input', 'input[name$="-qty"], input[name$="-unit_amount"]', recalcTotal);
    $(document).on('input', 'input[name$="-qty"], input[name$="-unit_amount"]', scheduleFeeSuggestionRefresh);
    $(document).on('change', 'input[name$="-DELETE"]', recalcTotal);
    $(document).on('change', 'input[name$="-DELETE"]', scheduleFeeSuggestionRefresh);

    // Purchase -> auto-fill/recompute service_fee_amount from the current line
    // items subtotal, clear research_fee_amount.
    // Research Fee -> auto-fill research_fee_amount (flat), clear service_fee_amount.
    // Expense Advance -> neither suggestion applies; clear both, staff enters manually.
    $(document).on('change', '#id_quotation_type', function () {
        var requestField = document.getElementById('id_request');
        var requestId = requestField ? requestField.value : null;

        if (this.value === 'purchase') {
            feeAutoFilled = true;
            refreshFeeSuggestion(feeField, requestId, computeLineItemsSubtotal());
            clearField(researchFeeField);
        } else if (this.value === 'research_fee') {
            fetchFlatSuggestionIfEmpty(researchFeeField, requestId);
            clearField(feeField);
        } else {
            clearField(feeField);
            clearField(researchFeeField);
        }
    });

    // Quotation Type defaults to 'purchase' with no change event ever firing
    // for it unless staff switch away and back — so picking a Request on a
    // fresh add form (the common case, type still at its default) needs its
    // own binding to trigger the suggestion for whichever type is active.
    // One fetch covers both concerns: filter the Quotation Type dropdown to
    // this request's allowed types, and (if the dropdown wasn't just reset,
    // which already handles its own fee-fill) fill the fee for the current type.
    $(document).on('change', '#id_request', function () {
        var requestId = this.value;
        if (!requestId) return;
        fetch(suggestedFeeBaseUrl() + 'suggested-fee/' + requestId + '/?product_value=' + computeLineItemsSubtotal())
            .then(function (r) { return r.json(); })
            .then(function (data) {
                notePremiumStatus(data);
                var wasReset = updateQuotationTypeOptions(data.allowed_quotation_types || ['purchase'], true);
                if (wasReset) return;
                var type = currentQuotationType();
                if (type === 'purchase' && data.fee !== null) {
                    feeAutoFilled = true;
                    feeField.value = data.fee;
                    recalcTotal();
                } else if (type === 'research_fee') {
                    fetchFlatSuggestionIfEmpty(researchFeeField, requestId);
                }
            });
    });

    recalcTotal();
    updateFieldVisibility();

    // Filter the dropdown to match an already-selected request on page load
    // too (e.g. opening an existing quotation's edit page) — allowReset is
    // false here since a saved quotation's current type was already valid
    // when it was saved; this only constrains the *next* pick, it never
    // rewrites an existing value.
    var initialRequestField = document.getElementById('id_request');
    if (initialRequestField && initialRequestField.value) {
        fetch(suggestedFeeBaseUrl() + 'suggested-fee/' + initialRequestField.value + '/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                notePremiumStatus(data);
                updateQuotationTypeOptions(data.allowed_quotation_types || ['purchase'], false);
                recalcTotal();
            });
    }
});
