/* ═══ AMILCAR App JS ═══ */

/* ═══ Back to Top ═══ */
(function(){
    var btn = document.getElementById('backToTop');
    if (!btn) return;
    var ticking = false;
    window.addEventListener('scroll', function(){
        if (!ticking) {
            requestAnimationFrame(function(){
                btn.classList.toggle('visible', window.scrollY > 300);
                ticking = false;
            });
            ticking = true;
        }
    }, {passive: true});
})();

/* ═══ Counter Animation for Stat Cards ═══ */
(function(){
    var observer = new IntersectionObserver(function(entries){
        entries.forEach(function(e){
            if (!e.isIntersecting) return;
            var el = e.target;
            observer.unobserve(el);
            var text = el.textContent.trim();
            var num = parseFloat(text.replace(/[^\d.-]/g, ''));
            if (isNaN(num) || num === 0) return;
            var suffix = text.replace(/[\d.,\s-]+/, '');
            var prefix = text.match(/^[^\d-]*/)[0];
            var isFloat = text.indexOf('.') > -1 || text.indexOf(',') > -1;
            var dec = isFloat ? (text.split(/[.,]/)[1] || '').length : 0;
            var start = 0, duration = 600, startTime = null;
            function step(ts){
                if (!startTime) startTime = ts;
                var p = Math.min((ts - startTime) / duration, 1);
                var ease = 1 - Math.pow(1 - p, 3);
                var v = start + (num - start) * ease;
                el.textContent = prefix + (isFloat ? v.toFixed(dec) : Math.round(v)) + suffix;
                if (p < 1) requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
        });
    }, {threshold: 0.3});
    document.querySelectorAll('.stat-card h2').forEach(function(h){ observer.observe(h); });
})();

/* ═══ Flash Message Auto-Dismiss ═══ */
(function(){
    document.querySelectorAll('.flash-success, .flash-info, .flash-warning').forEach(function(el){
        el.classList.add('flash-auto-dismiss');
    });
})();

/* ═══ Form Submit Loading State ═══ */
(function(){
    document.querySelectorAll('form').forEach(function(form){
        if (form.closest('.sidebar') || form.closest('.topbar-mobile')) return;
        form.addEventListener('submit', function(){
            var btn = form.querySelector('.btn-gold[type="submit"], button.btn-gold');
            if (btn && !btn.classList.contains('loading')) {
                btn.classList.add('loading');
                setTimeout(function(){ btn.classList.remove('loading'); }, 8000);
            }
        });
    });
})();

/* ═══ Custom Confirm Dialog ═══ */
window.confirmAction = function(message, onConfirm) {
    var overlay = document.createElement('div');
    overlay.className = 'confirm-dialog';
    overlay.innerHTML = '<div class="confirm-dialog-box">' +
        '<div class="confirm-icon">⚠</div>' +
        '<div class="confirm-title">Confirmer</div>' +
        '<div class="confirm-text">' + message + '</div>' +
        '<div class="confirm-actions">' +
        '<button class="btn-ghost btn-sm" id="confirmNo">Annuler</button>' +
        '<button class="btn-danger" id="confirmYes" style="padding:8px 20px">Confirmer</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector('#confirmNo').onclick = function(){ overlay.remove(); };
    overlay.querySelector('#confirmYes').onclick = function(){ overlay.remove(); onConfirm(); };
    overlay.addEventListener('click', function(e){ if (e.target === overlay) overlay.remove(); });
};

/* ═══ Enhanced Delete Confirmations ═══ */
document.querySelectorAll('form[onsubmit*="confirm"]').forEach(function(form){
    var origMsg = (form.getAttribute('onsubmit') || '').match(/confirm\(['"](.+?)['"]\)/);
    if (!origMsg) return;
    var msg = origMsg[1];
    form.removeAttribute('onsubmit');
    form.addEventListener('submit', function(e){
        e.preventDefault();
        confirmAction(msg, function(){ form.submit(); });
    });
});

/* ═══ Stagger Animation for Stat Grids ═══ */
document.querySelectorAll('.row.g-3').forEach(function(row){
    if (row.querySelector('.stat-card, .quick-stat')) row.classList.add('stagger');
});
