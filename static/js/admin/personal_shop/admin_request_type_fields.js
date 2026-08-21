document.addEventListener('DOMContentLoaded', function () {
    var typeField = document.getElementById('id_request_type');
    if (!typeField) return;

    // Mirrors PersonalShopRequest.TYPE_DETAIL_FIELDS' primary field per
    // request_type — a field hidden here is a field that mapping ignores.
    var FIELDS_BY_TYPE = {
        product_link: ['id_product_url'],
        boutique_purchase: ['id_boutique_name', 'id_source_parcel'],
        local_shop_purchase: ['id_shop_name'],
    };
    var ALL_TOGGLABLE_FIELD_IDS = ['id_product_url', 'id_shop_name', 'id_boutique_name', 'id_source_parcel'];

    // Finds the field's whole row/group (label + input + help text), not just
    // the input, so hiding it doesn't leave an orphaned label behind.
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

    function updateFieldVisibility() {
        var visible = FIELDS_BY_TYPE[typeField.value] || [];
        ALL_TOGGLABLE_FIELD_IDS.forEach(function (id) {
            var row = fieldRow(id);
            if (row) row.style.display = visible.indexOf(id) === -1 ? 'none' : '';
        });
    }

    typeField.addEventListener('change', updateFieldVisibility);
    updateFieldVisibility();
});
