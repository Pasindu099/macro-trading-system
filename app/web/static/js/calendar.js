(function () {
  const form = document.getElementById('calendar-filters');
  if (!form) return;
  const preset = document.getElementById('calendar-preset');
  document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
    preset.value = button.dataset.preset;
    form.requestSubmit();
  }));
  form.querySelectorAll('input[type=date]').forEach(input => input.addEventListener('change', () => {
    preset.value = 'custom';
  }));
  const cookie = name => document.cookie.split('; ').find(part => part.startsWith(name + '='))?.split('=')[1];
  if (!cookie('calendar_tz_name')) {
    document.cookie = 'calendar_tz_name=' + encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone) + '; path=/; SameSite=Lax';
  }
  const mode = cookie('calendar_tz_mode') || 'utc';
  document.querySelectorAll('[data-zone]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.zone === mode));
    button.addEventListener('click', () => {
      document.cookie = 'calendar_tz_mode=' + button.dataset.zone + '; path=/; SameSite=Lax';
      window.location.reload();
    });
  });
})();
