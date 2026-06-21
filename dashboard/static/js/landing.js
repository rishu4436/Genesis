(function () {
    'use strict';

    function initNav() {
        var nav = document.querySelector('.landing-nav');
        var toggle = document.querySelector('.nav-toggle');
        var links = document.querySelector('.nav-links');

        if (nav) {
            window.addEventListener('scroll', function () {
                nav.classList.toggle('scrolled', window.scrollY > 20);
            }, { passive: true });
        }

        if (toggle && links) {
            toggle.addEventListener('click', function () {
                links.classList.toggle('open');
            });
            links.querySelectorAll('a').forEach(function (a) {
                a.addEventListener('click', function () {
                    links.classList.remove('open');
                });
            });
        }
    }

    function initReveal() {
        var targets = document.querySelectorAll(
            '.stat-card, .feature-card, .step, .reveal'
        );
        if (!targets.length) return;

        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });

        targets.forEach(function (el, i) {
            if (el.classList.contains('feature-card') || el.classList.contains('stat-card')) {
                el.style.transitionDelay = (i % 6) * 0.08 + 's';
            }
            if (el.classList.contains('step')) {
                el.style.transitionDelay = (parseInt(el.dataset.step || '0', 10) * 0.1) + 's';
            }
            observer.observe(el);
        });
    }

    function initParticles() {
        var container = document.getElementById('bg-particles');
        if (!container) return;

        var count = window.matchMedia('(max-width: 768px)').matches ? 18 : 36;
        for (var i = 0; i < count; i++) {
            var p = document.createElement('span');
            p.className = 'bg-particle';
            p.style.left = (Math.random() * 100) + '%';
            p.style.bottom = (-10 - Math.random() * 20) + '%';
            p.style.animationDuration = (8 + Math.random() * 14) + 's';
            p.style.animationDelay = (Math.random() * 12) + 's';
            p.style.width = p.style.height = (2 + Math.random() * 2) + 'px';
            container.appendChild(p);
        }
    }

    function init() {
        initNav();
        initReveal();
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            initParticles();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();