/**
 * Shared submit-button loading state: disables the button, locks its width,
 * shows a spinner, and blocks a second submit while the first is in flight.
 *
 * Native forms: add `data-guard-submit` to the <form>. Handled automatically.
 * JS-driven actions (e.g. Razorpay checkout): call CamelTrunkLoading.start/stop directly.
 */
(function () {
    function startButtonLoading(btn) {
        if (!btn || btn.dataset.loading === 'true') {
            return;
        }
        btn.dataset.loading = 'true';
        btn.style.minWidth = btn.getBoundingClientRect().width + 'px';
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');

        var spinner = document.createElement('span');
        spinner.className = 'btn-spinner';
        spinner.setAttribute('aria-hidden', 'true');
        btn.prepend(spinner);
    }

    function stopButtonLoading(btn) {
        if (!btn) {
            return;
        }
        delete btn.dataset.loading;
        btn.disabled = false;
        btn.style.minWidth = '';
        btn.removeAttribute('aria-busy');

        var spinner = btn.querySelector('.btn-spinner');
        if (spinner) {
            spinner.remove();
        }
    }

    function showError(btn, message) {
        var targetId = btn.dataset.errorTarget;
        var target = targetId ? document.getElementById(targetId) : null;

        if (target) {
            target.textContent = message;
            target.hidden = false;
            return;
        }

        var existing = btn.parentElement.querySelector('.form-loading-error');
        if (existing) {
            existing.remove();
        }
        var el = document.createElement('div');
        el.className = 'modern-alert alert-danger form-loading-error';
        el.setAttribute('role', 'alert');
        el.textContent = message;
        btn.insertAdjacentElement('afterend', el);
    }

    function clearError(btn) {
        var targetId = btn.dataset.errorTarget;
        var target = targetId ? document.getElementById(targetId) : null;
        if (target) {
            target.hidden = true;
            return;
        }
        var existing = btn.parentElement.querySelector('.form-loading-error');
        if (existing) {
            existing.remove();
        }
    }

    function resetGuardedForms() {
        document.querySelectorAll('form[data-guard-submit]').forEach(function (form) {
            delete form.dataset.submitting;
            var btn = form.querySelector('button[type="submit"]');
            if (btn) {
                stopButtonLoading(btn);
            }
        });
    }

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.matches('[data-guard-submit]')) {
            return;
        }
        if (form.dataset.submitting === 'true') {
            e.preventDefault();
            return;
        }
        form.dataset.submitting = 'true';
        var btn = form.querySelector('button[type="submit"]');
        if (btn) {
            startButtonLoading(btn);
        }
    });

    // Full-page reloads make this a rare path (bfcache restore, back button),
    // but a stuck disabled button with no way forward is a real dead end.
    window.addEventListener('pageshow', function (e) {
        if (e.persisted) {
            resetGuardedForms();
        }
    });

    window.CamelTrunkLoading = {
        start: startButtonLoading,
        stop: stopButtonLoading,
        showError: showError,
        clearError: clearError,
    };
})();
