(function () {
  'use strict';

  // ============================================
  // Mobile navigation toggle
  // ============================================
  const toggle = document.getElementById('navToggle');
  const nav = document.getElementById('siteNav');

  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      const isOpen = nav.classList.toggle('open');
      toggle.classList.toggle('open', isOpen);
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    nav.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () {
        nav.classList.remove('open');
        toggle.classList.remove('open');
        toggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // ============================================
  // Header shadow on scroll
  // ============================================
  const header = document.getElementById('siteHeader');
  if (header) {
    const onScroll = function () {
      if (window.scrollY > 8) {
        header.style.boxShadow = '0 2px 12px rgba(0,0,0,0.06)';
      } else {
        header.style.boxShadow = 'none';
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // ============================================
  // Scroll-in animations
  // ============================================
  const animEls = document.querySelectorAll('[data-anim]');
  if ('IntersectionObserver' in window && animEls.length) {
    const io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('on');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });
    animEls.forEach(function (el) { io.observe(el); });
  } else {
    animEls.forEach(function (el) { el.classList.add('on'); });
  }

  // ============================================
  // Entry form: prefill job from query string
  // ============================================
  const jobSelect = document.getElementById('job');
  if (jobSelect) {
    const params = new URLSearchParams(window.location.search);
    const job = params.get('job');
    if (job) {
      const opt = jobSelect.querySelector('option[value="' + CSS.escape(job) + '"]');
      if (opt) {
        jobSelect.value = job;
      }
    }
  }

  // ============================================
  // Entry form: client-side validation & submit
  // ============================================
  const form = document.getElementById('entryForm');
  const note = document.getElementById('formNote');

  if (form && note) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      note.hidden = false;

      const name = form.name.value.trim();
      const kana = form.kana.value.trim();
      const email = form.email.value.trim();
      const job = form.job.value;
      const type = form.querySelector('input[name="type"]:checked');
      const agree = form.agree.checked;

      if (!name || !kana || !email || !job || !type || !agree) {
        note.textContent = '必須項目が未入力です。お手数ですがご確認ください。';
        note.className = 'form-note ng';
        return;
      }

      const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRe.test(email)) {
        note.textContent = 'メールアドレスの形式が正しくありません。';
        note.className = 'form-note ng';
        return;
      }

      note.textContent = 'ご応募ありがとうございます。3営業日以内にご登録のメールアドレス宛てにご連絡いたします。';
      note.className = 'form-note ok';
      form.reset();
      window.scrollTo({ top: form.offsetTop - 80, behavior: 'smooth' });
    });
  }
})();
