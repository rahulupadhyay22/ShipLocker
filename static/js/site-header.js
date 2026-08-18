(function () {
    var toggle = document.getElementById('mobileMenuToggle');
    var panel = document.getElementById('mobilePanel');
    var header = document.getElementById('siteHeader');

    function setMenuState(isOpen) {
        if (!panel || !toggle) {
            return;
        }

        panel.classList.toggle('open', isOpen);
        toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');

        if (header) {
            header.classList.toggle('menu-open', isOpen);
        }

        document.body.classList.toggle('menu-open', isOpen);
    }

    if (toggle && panel) {
        toggle.addEventListener('click', function () {
            var isOpen = !panel.classList.contains('open');
            setMenuState(isOpen);
        });

        panel.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                setMenuState(false);
            });
        });

        window.addEventListener('resize', function () {
            if (window.innerWidth > 1024) {
                setMenuState(false);
            }
        });
    }

    function onScroll() {
        if (!header) {
            return;
        }
        header.classList.toggle('scrolled', window.scrollY > 48);
    }

    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
})();
