/* ============================================================================
   SKILLS.html behaviour
   ----------------------------------------------------------------------------
   Source of truth for the page's script. build_skills_index.py reads this file
   at generation time and inlines it into the page head as a script element, so
   the page stays a single self-contained file with no external requests.

   Why <head>: the theme must be applied before the first paint, otherwise the
   page flashes the wrong colours. Everything that touches the DOM is deferred
   to DOMContentLoaded.
   ========================================================================= */

(function () {
  'use strict';

  var THEME_KEY = 'skills-index:theme';
  var MODES = ['auto', 'light', 'dark'];
  var root = document.documentElement;

  function prefersDark() {
    return !!(window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  /* localStorage throws on some file:// and private-mode setups, so never let
     that break the page: fall back to following the system preference. */
  function readMode() {
    try {
      var stored = window.localStorage.getItem(THEME_KEY);
      return MODES.indexOf(stored) !== -1 ? stored : 'auto';
    } catch (err) {
      return 'auto';
    }
  }

  function writeMode(value) {
    try {
      window.localStorage.setItem(THEME_KEY, value);
    } catch (err) {
      /* not persistable; the toggle still works for this page view */
    }
  }

  var mode = readMode();

  function effectiveMode(value) {
    if (value !== 'auto') return value;
    return prefersDark() ? 'dark' : 'light';
  }

  function paint() {
    root.setAttribute('data-theme', effectiveMode(mode));
    root.setAttribute('data-theme-mode', mode);
    var buttons = document.querySelectorAll('.theme-btn');
    for (var i = 0; i < buttons.length; i++) {
      var isActive = buttons[i].getAttribute('data-theme-mode') === mode;
      buttons[i].setAttribute('aria-pressed', isActive ? 'true' : 'false');
    }
  }

  /* Runs during head parsing: sets data-theme before the body is painted. */
  paint();

  /* Keep following the system while the user has not pinned a theme. */
  if (window.matchMedia) {
    var query = window.matchMedia('(prefers-color-scheme: dark)');
    var onSystemChange = function () {
      if (mode === 'auto') paint();
    };
    if (query.addEventListener) {
      query.addEventListener('change', onSystemChange);
    } else if (query.addListener) {
      query.addListener(onSystemChange);
    }
  }

  function applyFilter(input) {
    var q = input.value.trim().toLowerCase();
    var cards = document.querySelectorAll('.skill-card');
    var i;
    for (i = 0; i < cards.length; i++) {
      cards[i].style.display =
        (!q || cards[i].dataset.text.indexOf(q) !== -1) ? '' : 'none';
    }
    var links = document.querySelectorAll('.sidebar-link');
    for (i = 0; i < links.length; i++) {
      links[i].style.display =
        (!q || links[i].dataset.text.indexOf(q) !== -1) ? '' : 'none';
    }
    var sections = document.querySelectorAll('.category-section');
    for (i = 0; i < sections.length; i++) {
      var visibleCards =
        sections[i].querySelectorAll('.skill-card:not([style*="display: none"])');
      sections[i].style.display = visibleCards.length ? '' : 'none';
    }
    var groups = document.querySelectorAll('.sidebar-group');
    for (i = 0; i < groups.length; i++) {
      var visibleLinks =
        groups[i].querySelectorAll('.sidebar-link:not([style*="display: none"])');
      groups[i].style.display = visibleLinks.length ? '' : 'none';
    }
  }

  /* navigator.clipboard is undefined on file:// (not a secure context), so fall
     back to the legacy textarea + execCommand path. */
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.top = '-1000px';
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try {
        ok = document.execCommand('copy');
      } catch (err) {
        ok = false;
      }
      document.body.removeChild(area);
      if (ok) resolve(); else reject(new Error('copy failed'));
    });
  }

  function wireExamplePrompts() {
    var blocks = document.querySelectorAll('.examples');
    for (var b = 0; b < blocks.length; b++) {
      var block = blocks[b];
      var label = block.getAttribute('data-copy-label') || '';
      var copiedLabel = block.getAttribute('data-copied-label') || label;
      var buttons = block.querySelectorAll('.example-prompt');
      for (var i = 0; i < buttons.length; i++) {
        (function (button) {
          button.addEventListener('click', function () {
            var chip = button.querySelector('.example-copy');
            var settle = function (ok) {
              if (!chip) return;
              chip.textContent = ok ? copiedLabel : label;
              if (ok) button.classList.add('is-copied');
              window.setTimeout(function () {
                chip.textContent = label;
                button.classList.remove('is-copied');
              }, 1400);
            };
            copyText(button.getAttribute('data-copy') || '')
              .then(function () { settle(true); }, function () { settle(false); });
          });
        })(buttons[i]);
      }
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var buttons = document.querySelectorAll('.theme-btn');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function (ev) {
        var next = ev.currentTarget.getAttribute('data-theme-mode') || 'auto';
        mode = MODES.indexOf(next) !== -1 ? next : 'auto';
        writeMode(mode);
        paint();
      });
    }
    paint();
    wireExamplePrompts();

    var input = document.getElementById('search');
    if (!input) return;
    input.addEventListener('input', function () {
      applyFilter(input);
    });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && input.value) {
        input.value = '';
        applyFilter(input);
      }
    });
  });
})();
